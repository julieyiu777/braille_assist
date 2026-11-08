"""Durable local camera collection, analysis, and required Tiger Data sync."""
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
FIELDS = ('observed_at', 'event_id', 'session_id', 'device_id', 'kind', 'label',
          'description', 'severity', 'busyness', 'fingerprint', 'latitude', 'longitude', 'payload')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def database_url():
    """Read the environment, or only TIGER_DATABASE_URL from a local .env file."""
    value = os.environ.get('TIGER_DATABASE_URL')
    if value:
        return value
    path = ROOT/'.env'
    if path.exists():
        for line in path.read_text().splitlines():
            key, separator, value = line.removeprefix('export ').partition('=')
            if separator and key.strip() == 'TIGER_DATABASE_URL':
                return value.strip().strip('\"\'') or None
    return None


def connect_tiger():
    import psycopg
    url = database_url()
    if not url:
        raise RuntimeError('Set TIGER_DATABASE_URL in the environment or .env first')
    # Do not print connection exceptions: drivers can include credentials/hosts.
    return psycopg.connect(url, connect_timeout=5, options='-c statement_timeout=10000')


def require_tiger():
    """Validate the required database before opening the camera or serial port."""
    if not database_url():
        raise RuntimeError('Tiger Data is required. Set TIGER_DATABASE_URL in the environment or .env, then run python tiger_cli.py init.')
    try:
        with connect_tiger() as connection:
            connection.execute(f'SELECT {",".join(FIELDS)} FROM makecu.events LIMIT 0')
            writable = connection.execute(
                "SELECT has_table_privilege(current_user, 'makecu.events', 'INSERT')").fetchone()[0]
            if not writable:
                raise RuntimeError('Missing INSERT permission')
    except Exception as error:
        # Inspect internally; never expose driver messages containing credentials.
        if (getattr(error, 'sqlstate', None) == '28P01'
                or 'password authentication failed' in str(error).lower()):
            raise RuntimeError(
                'Tiger Data rejected the database password. Run '
                'python update_tiger_password.py and enter the current database '
                'password from Tiger Console, then restart. If TIGER_DATABASE_URL '
                'is exported in your terminal, unset it so the app uses .env.'
            ) from None
        raise RuntimeError('Tiger Data is required but its connection, schema, or permissions are not ready. Check TIGER_DATABASE_URL and run python tiger_cli.py init.') from None


def model_digest(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return f'{int("".join("1" if bit else "0" for bit in bits), 2):016x}'


def hash_distance(first, second):
    return (int(first, 16)^int(second, 16)).bit_count()


class SceneMeter:
    """A motion/clutter heuristic, not a hazard or object-count detector."""
    def __init__(self):
        self.previous = None
        self.smoothed = 0.

    def update(self, frame):
        gray = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = float(np.mean(cv2.Canny(gray, 60, 120) > 0))
        motion = 0. if self.previous is None else float(np.mean(cv2.absdiff(gray, self.previous) > 20))
        self.previous = gray
        value = min(1., .8*motion+.2*min(1., edges/.2))
        self.smoothed = .5*self.smoothed+.5*value
        return round(self.smoothed, 4)


def delivery_plan(message, severity, busyness, cells_per_second=2.):
    if not 0 <= severity <= 3 or not 0 <= busyness <= 1 or cells_per_second <= 0:
        raise ValueError('Invalid delivery inputs')
    allowed = severity >= 2 or busyness < .65
    # Hardware encoders must replace this estimated cell count with actual cells.
    cells = len(message)
    return dict(message=message, allowed=allowed, priority=severity,
                detail='essential' if busyness >= .65 else 'normal',
                suggested_seconds=round(max(1., min(60., cells/cells_per_second)), 2),
                estimated_cells=cells, actuated=False)


class EventStore:
    def __init__(self, path='runs/telemetry/events.sqlite3', device_id='camera-1', session_id=None):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.device_id = device_id
        self.session_id = session_id or str(uuid.uuid4())
        uuid.UUID(self.session_id)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL,
                session_id TEXT NOT NULL, device_id TEXT NOT NULL, kind TEXT NOT NULL,
                label TEXT, description TEXT NOT NULL, severity INTEGER NOT NULL,
                busyness REAL, fingerprint TEXT, latitude REAL, longitude REAL,
                payload TEXT NOT NULL, synced INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS local_events_time ON events(device_id,observed_at);
            CREATE INDEX IF NOT EXISTS local_events_sync ON events(synced,observed_at);
            CREATE TABLE IF NOT EXISTS scenes (
                scene_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, labels TEXT NOT NULL, last_seen TEXT NOT NULL
            );
        ''')

    def record(self, kind, *, label=None, description='', severity=0, busyness=None,
               scene_hash=None, location=None, payload=None, observed_at=None, event_id=None):
        if not 0 <= severity <= 3 or (busyness is not None and not 0 <= busyness <= 1):
            raise ValueError('severity/busyness out of range')
        lat, lon = location if location is not None else (None, None)
        if location is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError('Invalid latitude/longitude')
        event_id = event_id or str(uuid.uuid4())
        uuid.UUID(event_id)
        timestamp = observed_at or utc_now()
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            raise ValueError('Timestamp must include its timezone')
        timestamp = parsed.astimezone(timezone.utc).isoformat()
        values = dict(observed_at=timestamp, event_id=event_id, session_id=self.session_id,
                      device_id=self.device_id, kind=kind, label=label, description=description,
                      severity=severity, busyness=busyness, fingerprint=scene_hash,
                      latitude=lat, longitude=lon, payload=json.dumps(payload or {}, allow_nan=False))
        self.db.execute(f'INSERT OR IGNORE INTO events ({",".join(FIELDS)}) VALUES ({",".join("?" for _ in FIELDS)})',
                        [values[key] for key in FIELDS])
        self.db.commit()
        return event_id

    def rows(self, kind=None, session_id=None, limit=1000):
        clauses, values = ['device_id=?'], [self.device_id]
        if kind:
            clauses.append('kind=?')
            values.append(kind)
        if session_id:
            clauses.append('session_id=?')
            values.append(session_id)
        values.append(limit)
        return self.db.execute('SELECT * FROM events WHERE '+' AND '.join(clauses)+
                               ' ORDER BY observed_at DESC,event_id LIMIT ?', values).fetchall()

    def scene_changes(self, scene_hash, labels):
        labels = sorted(set(labels))
        scenes = self.db.execute('SELECT * FROM scenes WHERE device_id=? ORDER BY last_seen DESC LIMIT 1000',
                                 (self.device_id,)).fetchall()
        match = min(scenes, key=lambda row: hash_distance(row['fingerprint'], scene_hash)) if scenes else None
        if match is not None and hash_distance(match['fingerprint'], scene_hash) <= 6:
            before = set(json.loads(match['labels']))
            self.db.execute('UPDATE scenes SET labels=?,last_seen=? WHERE scene_id=?',
                            (json.dumps(labels), utc_now(), match['scene_id']))
            result = dict(seen_before=True, scene_id=match['scene_id'],
                          added=sorted(set(labels)-before), removed=sorted(before-set(labels)))
        else:
            identity = str(uuid.uuid4())
            self.db.execute('INSERT INTO scenes VALUES (?,?,?,?,?)',
                            (identity, self.device_id, scene_hash, json.dumps(labels), utc_now()))
            result = dict(seen_before=False, scene_id=identity, added=labels, removed=[])
        self.db.commit()
        return result

    def record_servo(self, pin, duration_ms, target, *, source='hardware'):
        if not pin or duration_ms <= 0 or not math.isfinite(duration_ms):
            raise ValueError('Pin and a finite measured duration > 0 are required')
        previous = [json.loads(row['payload'])['duration_ms'] for row in self.rows('servo', limit=1000)
                    if json.loads(row['payload'])['pin'] == str(pin)
                    and json.loads(row['payload']).get('source') == source][:20]
        # Use the older half as a baseline so gradual slowing is visible.
        baseline = float(np.median(previous[-10:])) if len(previous) >= 5 else None
        slow = baseline is not None and duration_ms > max(1.5*baseline, baseline+20)
        return self.record('servo', severity=2 if slow else 0,
                           description=f'Pin {pin} {target}'+(' is slower than its baseline' if slow else ''),
                           payload=dict(pin=str(pin), duration_ms=duration_ms, target=target,
                                        baseline_ms=baseline, slow=slow, source=source))

    def record_reading(self, user_id, cells, seconds, correct=True):
        if cells <= 0 or seconds <= 0 or not math.isfinite(seconds):
            raise ValueError('Measured cells/seconds must be positive')
        return self.record('reading', description=f'Braille reading feedback for {user_id}',
                           payload=dict(user_id=user_id, cells=cells, seconds=seconds, correct=bool(correct)))

    def reading_rate(self, user_id):
        samples = [json.loads(row['payload']) for row in self.rows('reading', limit=1000)]
        samples = [sample for sample in samples if sample['user_id'] == user_id and sample['correct']][:20]
        if not samples:
            return 2.
        return sum(sample['cells'] for sample in samples)/sum(sample['seconds'] for sample in samples)

    def search(self, query, limit=20):
        ignored = {'where', 'was', 'were', 'the', 'a', 'an', 'is', 'my', 'did', 'i', 'see'}
        import re
        words = [word for word in re.findall(r'\w+', query.lower()) if word not in ignored]
        if not words:
            return []
        clauses = ['device_id=?', "(kind!='detection' OR json_extract(payload,'$.inference_fresh')=1)"]+[
            "lower(coalesce(label,'') || ' ' || description) LIKE ? ESCAPE '\\'" for _ in words]
        values = [self.device_id]+['%'+word.replace('_', '\\_')+'%' for word in words]+[limit]
        return self.db.execute('SELECT * FROM events WHERE '+' AND '.join(clauses)+
                               ' ORDER BY observed_at DESC LIMIT ?', values).fetchall()

    def nearby_hazards(self, latitude, longitude, radius_m=50):
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or not math.isfinite(radius_m) or radius_m <= 0:
            raise ValueError('Valid coordinates and a positive radius are required')
        results = []
        groups = {}
        for row in self.rows('hazard', limit=10000):
            if row['latitude'] is None or row['longitude'] is None:
                continue
            key = (row['label'], round(row['latitude'], 4), round(row['longitude'], 4))
            group = groups.setdefault(key, dict(label=row['label'], latitude=row['latitude'],
                longitude=row['longitude'], sightings=0, sessions=set(), severity=0, last_seen=row['observed_at']))
            group['sightings'] += 1
            group['sessions'].add(row['session_id'])
            group['severity'] = max(group['severity'], row['severity'])
        for group in groups.values():
            lat1, lat2 = math.radians(latitude), math.radians(group['latitude'])
            dlat, dlon = lat2-lat1, math.radians(group['longitude']-longitude)
            distance = 6371000*2*math.asin(min(1., math.sqrt(math.sin(dlat/2)**2+
                                         math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2)))
            if distance <= radius_m:
                results.append(dict(group, sessions=len(group['sessions']), distance_m=round(distance, 1)))
        return sorted(results, key=lambda group: group['distance_m'])

    def close(self):
        self.db.close()


class CaptureLog:
    def __init__(self, store, model_path, roi, user_id='default', save_frames=True,
                 source='camera', threshold=None, margin=None, targets=None):
        self.store, self.roi, self.user_id = store, list(roi), user_id
        self.model_hash = model_digest(model_path)
        self.model_path = str(Path(model_path).resolve())
        self.directory = store.path.parent/'frames'/store.session_id
        if save_frames:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.save_frames = save_frames
        self.meter = SceneMeter()
        self.sequence = 0
        self.last_inference_at = None
        self.source = str(source)
        self.store.record('session', payload=dict(model_path=self.model_path, model_hash=self.model_hash,
            roi=self.roi, frames_saved=save_frames, input_source=self.source,
            threshold=threshold, margin=margin, targets=targets,
            confirmation_seconds=3, lock_seconds=100))

    def record_frame(self, frame, label, scores, bounds, *, fresh, confirmed, locked,
                     captured_at=None, location=None):
        captured_at = captured_at or utc_now()
        self.sequence += 1
        identity = str(uuid.uuid4())
        path = None
        if self.save_frames:
            target = self.directory/f'{self.sequence:09}_{identity}.jpg'
            if not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 90]):
                raise OSError('Cannot archive camera frame')
            path = str(target)
        if fresh:
            self.last_inference_at = captured_at
        scene_hash, busy = fingerprint(frame), self.meter.update(frame)
        payload = dict(sequence=self.sequence, frame_path=path, roi=self.roi, input_source=self.source,
                       frame_width=frame.shape[1], frame_height=frame.shape[0],
                       scores=[float(value) for value in scores], bounds=[int(value) for value in bounds] if bounds else None,
                       model_hash=self.model_hash, model_path=self.model_path,
                       inference_fresh=fresh, inference_at=self.last_inference_at,
                       confirmed=confirmed, locked=locked)
        self.store.record('detection', label=label, description=label or 'No accepted target',
            busyness=busy, scene_hash=scene_hash, observed_at=captured_at, location=location,
            payload=payload, event_id=identity)
        if confirmed and label:
            changes = self.store.scene_changes(scene_hash, [label])
            message = ', '.join(changes['added'])
            if changes['removed']:
                message += '; no longer visible: '+', '.join(changes['removed'])
            rate = self.store.reading_rate(self.user_id)
            plan = delivery_plan(message, 0, busy, rate)
            if not message:
                plan['allowed'] = False
            self.store.record('output_plan', label=label, description=message or 'Scene unchanged', busyness=busy,
                scene_hash=scene_hash, location=location,
                payload=dict(plan=plan, changes=changes, detection_id=identity, user_id=self.user_id))
        return busy


def sync_pending(store, connection, limit=200):
    from psycopg.types.json import Jsonb
    from psycopg.pq import TransactionStatus
    rows = store.db.execute('SELECT * FROM events WHERE synced=0 ORDER BY observed_at,event_id LIMIT ?', (limit,)).fetchall()
    if not rows:
        return 0
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError('Sync requires an idle connection so local acknowledgment follows a real commit')
    query = f'INSERT INTO makecu.events ({",".join(FIELDS)}) VALUES ({",".join("%s" for _ in FIELDS)}) ON CONFLICT DO NOTHING'
    values = [[Jsonb(json.loads(row[key])) if key == 'payload' else row[key] for key in FIELDS] for row in rows]
    # A commit before local acknowledgement makes retry after a crash idempotent.
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.executemany(query, values)
    store.db.executemany('UPDATE events SET synced=1 WHERE event_id=?', [(row['event_id'],) for row in rows])
    store.db.commit()
    return len(rows)


class TigerSync:
    def __init__(self, store):
        self.path, self.device_id = store.path, store.device_id
        self.stop = threading.Event()
        self.failed = threading.Event()
        self.thread = None
        # Verify a real committed upload, including this session, before capture.
        try:
            with connect_tiger() as connection:
                sync_pending(store, connection)
        except Exception:
            raise RuntimeError('Tiger Data startup upload failed; unsent events remain in the local outbox.') from None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def check_health(self):
        if self.failed.is_set():
            raise RuntimeError('Tiger Data upload failed; detection stopped. Unsent events remain in the local outbox. Restore the connection and restart.')

    def run(self):
        store, connection = EventStore(self.path, self.device_id), None
        try:
            while not self.stop.is_set():
                try:
                    if connection is None:
                        connection = connect_tiger()
                    count = sync_pending(store, connection)
                    self.stop.wait(.1 if count else .5)
                except Exception:
                    self.failed.set()
                    if connection is not None:
                        connection.close()
                        connection = None
                    break
        finally:
            if connection is not None:
                connection.close()
            store.close()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=6)
