"""Collect hardware feedback, search captures, replay models, and manage Tiger Data."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

from tiger_data import EventStore, ROOT, connect_tiger, database_url, model_digest, sync_pending, utc_now


def as_dict(row):
    result = dict(row)
    if isinstance(result.get('payload'), str):
        result['payload'] = json.loads(result['payload'])
    return result


def emit(value):
    print(json.dumps(value, default=str, indent=2))


def cloud_search(connection, query, device_id):
    from psycopg.rows import dict_row
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""SELECT observed_at,session_id,label,description,latitude,longitude,payload
            FROM makecu.events WHERE device_id=%s AND
            (kind!='detection' OR (payload->>'inference_fresh')::boolean) AND
            to_tsvector('english',coalesce(label,'') || ' ' || description) @@ websearch_to_tsquery('english',%s)
            ORDER BY observed_at DESC LIMIT 20""", (device_id, query))
        return cursor.fetchall()


def review_export(store, output, limit):
    from tiger_data import hash_distance
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    selected, hashes = [], []
    for row in store.rows('detection', limit=100000):
        payload = json.loads(row['payload'])
        if not payload['inference_fresh'] or not payload.get('frame_path'):
            continue
        values = sorted(payload['scores'], reverse=True)
        uncertain = (row['label'] is None or payload.get('confirmed', False)
                     or (values and values[0] < .95) or (len(values)>1 and values[0]-values[1] < .4))
        if not uncertain:
            continue
        if row['fingerprint'] and any(hash_distance(row['fingerprint'], old) <= 6 for old in hashes):
            continue
        source = Path(payload['frame_path'])
        if not source.is_file():
            continue
        dest = directory/(row['event_id']+'.jpg')
        shutil.copyfile(source, dest)
        selected.append(dict(event_id=row['event_id'], frame_path=str(dest.resolve()),
            session_id=row['session_id'], observed_at=row['observed_at'], model_hash=payload['model_hash'],
            predicted_label=row['label'], scores=payload['scores'], human_label=None, description=None))
        if row['fingerprint']:
            hashes.append(row['fingerprint'])
        if len(selected) >= limit:
            break
    (directory/'review.json').write_text(json.dumps(selected, indent=2))
    return dict(exported=len(selected), manifest=str(directory/'review.json'),
                note='Predictions are suggestions; human labels are required before training.')


def annotate(store, event_id, label, description):
    from roi_model import TARGETS
    if label not in [*TARGETS, 'negative']:
        raise ValueError('Training labels must be a supported target or negative')
    row = store.db.execute("SELECT * FROM events WHERE event_id=? AND kind='detection' AND device_id=?",
                           (event_id, store.device_id)).fetchone()
    if row is None:
        raise ValueError('Capture event not found for this device')
    return store.record('annotation', label=label, description=description,
        payload=dict(detection_id=event_id, frame_path=json.loads(row['payload']).get('frame_path'),
                     capture_session=row['session_id'], capture_time=row['observed_at'],
                     human_verified=True))


def export_labels(store, output):
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    seen, manifest = set(), []
    for row in store.rows('annotation', limit=100000):
        payload = json.loads(row['payload'])
        identity = payload['detection_id']
        if identity in seen or not payload.get('human_verified'):
            continue
        seen.add(identity)
        if row['label'] not in ('chips', 'soda', 'pens', 'negative'):
            continue
        source = Path(payload['frame_path']) if payload.get('frame_path') else None
        if source is None or not source.is_file():
            continue
        target = directory/row['label']/(identity+'.jpg')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest.append(dict(path=str(target), label=row['label'], detection_id=identity,
                             split_group=payload['capture_session'], human_verified=True))
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2))
    return dict(exported=len(manifest), manifest=str(directory/'manifest.json'),
                note='Split by capture session before training; no automatic training from model predictions.')


def replay(store, session, model_path, output, limit=1000):
    import cv2
    import torch
    from roi_detect import detect_product
    from roi_model import ROIClassifier
    torch.set_num_threads(4)
    classifier = ROIClassifier(model_path)
    digest = model_digest(model_path)
    annotations = {}
    for row in store.rows('annotation', limit=100000):
        payload = json.loads(row['payload'])
        if payload.get('human_verified'):
            annotations.setdefault(payload['detection_id'], row['label'])
    records, missing, correct, annotated = [], [], 0, 0
    for row in reversed(store.rows('detection', session_id=session, limit=limit)):
        payload = json.loads(row['payload'])
        frame = cv2.imread(payload['frame_path']) if payload.get('frame_path') and Path(payload['frame_path']).is_file() else None
        if frame is None:
            missing.append(row['event_id'])
            continue
        label, scores, bounds = detect_product(classifier, frame, payload['roi'])
        truth = annotations.get(row['event_id'])
        if truth is not None:
            annotated += 1
            correct += label == (None if truth == 'negative' else truth)
        result = dict(original_id=row['event_id'], original_time=row['observed_at'],
            original_label=row['label'], original_inference_fresh=payload['inference_fresh'],
            label=label, scores=scores.tolist(), bounds=[int(value) for value in bounds] if bounds else None,
            human_label=truth)
        records.append(result)
        store.record('replay', label=label, description='Replayed camera capture',
                     payload=dict(result, model_hash=digest, processed_at=utc_now()))
    report = dict(source_session=session, model_hash=digest, replayed=len(records), missing_frames=missing,
        label_changes=sum(row['original_label'] != row['label'] for row in records),
        human_annotated=annotated, correct_vs_human=correct if annotated else None,
        accuracy_vs_human=correct/annotated if annotated else None,
        note='Label changes measure disagreement, not accuracy. Cached original labels were not fresh inference.',
        frames=records)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    return {key: value for key, value in report.items() if key != 'frames'}


def listen_alerts(device_id):
    from psycopg.rows import dict_row
    # LISTEN is registered before reading pending rows, avoiding startup races.
    # Reconnecting consumers see unacknowledged rows even if NOTIFY was missed.
    delivered = set()
    with connect_tiger() as connection:
        connection.autocommit = True
        connection.execute('LISTEN makecu_alerts')
        while True:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute('SELECT * FROM makecu.alerts WHERE device_id=%s AND acknowledged_at IS NULL ORDER BY observed_at',
                               (device_id,))
                for row in cursor.fetchall():
                    identity = str(row['event_id'])
                    if identity not in delivered:
                        print(json.dumps(row, default=str), flush=True)
                        delivered.add(identity)
            for _ in connection.notifies(timeout=2, stop_after=1):
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='runs/telemetry/events.sqlite3')
    parser.add_argument('--device-id', default='camera-1')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('--postgres-only', action='store_true', help='Test plain PostgreSQL without TimescaleDB policies.')
    sub.add_parser('sync')
    sub.add_parser('status')
    search = sub.add_parser('search')
    search.add_argument('query')
    search.add_argument('--cloud', action='store_true')
    servo = sub.add_parser('servo')
    servo.add_argument('--pin', required=True)
    servo.add_argument('--duration-ms', type=float, required=True)
    servo.add_argument('--target', choices=('raised','lowered'), required=True)
    reading = sub.add_parser('reading')
    reading.add_argument('--user-id', default='default')
    reading.add_argument('--cells', type=int, required=True)
    reading.add_argument('--seconds', type=float, required=True)
    reading.add_argument('--incorrect', action='store_true')
    hazard = sub.add_parser('hazard')
    hazard.add_argument('--label', required=True)
    hazard.add_argument('--description', required=True)
    hazard.add_argument('--location', type=float, nargs=2, required=True)
    hazard.add_argument('--severity', type=int, choices=(0,1,2,3), default=2)
    nearby = sub.add_parser('nearby')
    nearby.add_argument('--location', type=float, nargs=2, required=True)
    nearby.add_argument('--radius-m', type=float, default=50)
    review = sub.add_parser('review-export')
    review.add_argument('--output', required=True)
    review.add_argument('--limit', type=int, default=100)
    annotation = sub.add_parser('annotate')
    annotation.add_argument('--event-id', required=True)
    annotation.add_argument('--label', required=True)
    annotation.add_argument('--description', required=True)
    export = sub.add_parser('export-labels')
    export.add_argument('--output', required=True)
    rep = sub.add_parser('replay')
    rep.add_argument('--session', required=True)
    rep.add_argument('--model', default='runs/rollerball_active/best.pt')
    rep.add_argument('--output', default='runs/replays/comparison.json')
    rep.add_argument('--limit', type=int, default=1000)
    sub.add_parser('listen')
    ack = sub.add_parser('ack-alert')
    ack.add_argument('--event-id', required=True)
    sub.add_parser('storage')
    args = parser.parse_args()
    store = EventStore(args.db, args.device_id)
    try:
        if args.command == 'init':
            with connect_tiger() as connection:
                connection.execute((ROOT/'tiger_schema.sql').read_text())
            if not args.postgres_only:
                with connect_tiger() as connection:
                    connection.execute((ROOT/'tiger_timescale.sql').read_text())
            emit(dict(schema='ready', timescale_policy=not args.postgres_only))
        elif args.command == 'sync':
            total = 0
            with connect_tiger() as connection:
                while count := sync_pending(store, connection):
                    total += count
            emit(dict(synced=total))
        elif args.command == 'status':
            total, pending = store.db.execute('SELECT count(*),coalesce(sum(synced=0),0) FROM events').fetchone()
            emit(dict(database_configured=bool(database_url()), local_database=str(store.path),
                      events=total, pending=pending,
                      kinds=[dict(row) for row in store.db.execute('SELECT kind,count(*) AS count FROM events GROUP BY kind')]))
        elif args.command == 'search':
            if args.cloud:
                with connect_tiger() as connection:
                    emit(cloud_search(connection, args.query, args.device_id))
            else:
                emit([as_dict(row) for row in store.search(args.query)])
        elif args.command == 'servo':
            emit(dict(event_id=store.record_servo(args.pin, args.duration_ms, args.target, source='manual_measurement')))
        elif args.command == 'reading':
            store.record_reading(args.user_id, args.cells, args.seconds, not args.incorrect)
            emit(dict(cells_per_second=store.reading_rate(args.user_id)))
        elif args.command == 'hazard':
            emit(dict(event_id=store.record('hazard', label=args.label, description=args.description,
                severity=args.severity, location=args.location, payload=dict(source='human_report'))))
        elif args.command == 'nearby':
            emit(store.nearby_hazards(*args.location, args.radius_m))
        elif args.command == 'review-export':
            if args.limit < 1:
                parser.error('limit must be positive')
            emit(review_export(store, args.output, args.limit))
        elif args.command == 'annotate':
            emit(dict(annotation_id=annotate(store, args.event_id, args.label, args.description)))
        elif args.command == 'export-labels':
            emit(export_labels(store, args.output))
        elif args.command == 'replay':
            if args.limit < 1:
                parser.error('limit must be positive')
            emit(replay(store, args.session, args.model, args.output, args.limit))
        elif args.command == 'listen':
            listen_alerts(args.device_id)
        elif args.command == 'ack-alert':
            uuid.UUID(args.event_id)
            with connect_tiger() as connection:
                cursor = connection.execute('UPDATE makecu.alerts SET acknowledged_at=now() WHERE event_id=%s AND device_id=%s AND acknowledged_at IS NULL',
                                            (args.event_id, args.device_id))
                emit(dict(acknowledged=cursor.rowcount))
        elif args.command == 'storage':
            from psycopg.rows import dict_row
            with connect_tiger() as connection, connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT * FROM hypertable_columnstore_stats('makecu.events')")
                emit(cursor.fetchall())
    except KeyboardInterrupt:
        pass
    except Exception as error:
        # Keep DSNs and other connection details out of logs and terminal output.
        print(f'Operation failed ({type(error).__name__}). Check local inputs or the Tiger Data connection/schema.', file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        store.close()


if __name__ == '__main__':
    main()
