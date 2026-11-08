"""Tiger Data sync + insights for Braille Vision.

1. Ships the detector's event queue (/workspace/braille-vision/events/queue.jsonl) into Tiger Cloud table
   braille_events, idempotently (ON CONFLICT id, time); a batch is deleted only after commit.
2. Reads Tiger's continuous aggregates (braille_minute, braille_label_minute; percentile_agg hyperfunctions,
   time_bucket_gapfill) and raw events to compute health signals: model latency, round trip, frame rate,
   ambiguity, label flicker, time to confirm, servo pace, errors, class coverage, wasted uploads.
3. Records non-ok signals in Tiger table braille_signals (warning history) and writes the derived summary to
   app/insights, which the app's Insights tab renders. Everything under app/insights is rebuildable from Tiger.
"""
import glob, json, os, time
import psycopg, requests
from psycopg.types.json import Jsonb
from firebase_helper import firebase
from sandbox_runtime_context import get_api_keys
from env import get_user_id

ROOT = "/workspace/braille-vision"
EVENT_DIR = os.path.join(ROOT, "events")
QUEUE = os.path.join(EVENT_DIR, "queue.jsonl")
OUT = f"{get_user_id()}/file-system/apps/braille-vision/app/insights"
TEST = ["mana-selftest"]
WINDOW = "15 minutes"

dsn = (get_api_keys().get("TIGER_DATABASE_URL") or "").strip()
if not dsn:
    raise RuntimeError("TIGER_DATABASE_URL is not saved in Mana keys")

try:
    port = int(open(os.path.join(ROOT, "PORT")).read().strip())
    health = requests.get(f"http://127.0.0.1:{port}/healthz", timeout=5).json()
except Exception:
    health = {}
classes = health.get("classes") or []
confirm_s = float(health.get("confirmSeconds") or 3)

# ---------- 1. ship queue ----------
os.makedirs(EVENT_DIR, exist_ok=True)
if os.path.exists(QUEUE) and os.path.getsize(QUEUE) > 0:
    os.rename(QUEUE, os.path.join(EVENT_DIR, f"batch-{int(time.time() * 1000)}.jsonl"))
    time.sleep(1)
COLS = ["time", "id", "kind", "session", "label", "confidence", "phase", "server_ms", "rtt_ms", "margin", "scores", "detail", "model"]
SQL = f"insert into braille_events ({', '.join(COLS)}) values ({', '.join('%(' + c + ')s' for c in COLS)}) on conflict (id, time) do nothing"
inserted = bad = 0
conn = psycopg.connect(dsn, connect_timeout=15, autocommit=True)
for path in sorted(glob.glob(os.path.join(EVENT_DIR, "batch-*.jsonl"))):
    rows = []
    for line in open(path):
        try:
            r = json.loads(line)
            row = {c: r.get(c) for c in COLS}
            row["scores"] = Jsonb(r["scores"]) if r.get("scores") else None
            row["detail"] = Jsonb(r["detail"]) if r.get("detail") else None
            rows.append(row)
        except Exception:
            bad += 1
    with conn.transaction(), conn.cursor() as cur:
        if rows:
            cur.executemany(SQL, rows)
    os.remove(path)
    inserted += len(rows)

# ---------- 2. metrics from Tiger ----------
def one(sql, **p):
    return conn.execute(sql, {"t": TEST, **p}).fetchone()
def allrows(sql, **p):
    return conn.execute(sql, {"t": TEST, **p}).fetchall()

AGG = """select coalesce(sum(frames),0), coalesce(sum(model_frames),0), coalesce(sum(locked_frames),0), coalesce(sum(confirms),0),
           coalesce(sum(ambiguous),0), coalesce(sum(errors),0), coalesce(sum(playbacks),0),
           approx_percentile(0.5, rollup(model_ms)), approx_percentile(0.95, rollup(model_ms)), approx_percentile(0.95, rollup(rtt)),
           count(*) filter (where frames > 0), coalesce(max(sessions),0)
         from braille_minute where bucket > now() - %(w)s::interval"""
keys = ["frames", "modelFrames", "lockedFrames", "confirms", "ambiguous", "errors", "playbacks", "p50", "p95", "rtt95", "activeMin", "sessions"]
num = lambda row: [None if v is None else float(v) for v in row]  # sums over aggregates arrive as Decimal
recent = dict(zip(keys, num(one(AGG, w=WINDOW))))
day = dict(zip(keys, num(one(AGG, w="24 hours"))))

flick = one("""select count(*) filter (where prev = 'confirming' and phase = 'scanning'), count(*) filter (where prev = 'confirming' and phase = 'locked')
   from (select phase, lag(phase) over (partition by session order by time) prev from braille_events
         where kind = 'frame' and time > now() - interval '15 minutes' and session <> all(%(t)s)) x""")
ttc = one("""with f as (select session, time, phase, lag(phase) over (partition by session order by time) prev from braille_events
              where kind = 'frame' and time > now() - interval '24 hours' and session <> all(%(t)s)),
   s as (select session, time from f where phase = 'confirming' and prev is distinct from 'confirming'),
   c as (select session, time from braille_events where kind = 'confirm' and time > now() - interval '24 hours' and session <> all(%(t)s))
   select percentile_cont(0.5) within group (order by extract(epoch from c.time - x.t)), count(*)
   from c cross join lateral (select max(s.time) t from s where s.session = c.session and s.time <= c.time) x where x.t is not null""")
servo = one("""select avg((detail->>'durationMs')::float / nullif((detail->>'cells')::float, 0)) / 1000, count(*),
     count(*) filter (where (detail->>'stopped')::bool)
   from braille_events where kind = 'playback' and (detail->>'hardware')::bool and time > now() - interval '24 hours' and session <> all(%(t)s)""")
errs = allrows("""select detail->>'source', detail->>'message', time from braille_events
   where kind = 'error' and time > now() - interval '24 hours' and session <> all(%(t)s) order by time desc limit 20""")
last_frame = one("select max(time) from braille_events where kind = 'frame' and session <> all(%(t)s)")[0]
labels = allrows("""select label, sum(model_frames), sum(avg_conf * model_frames) / nullif(sum(model_frames) filter (where avg_conf is not null), 0),
     sum(avg_margin * model_frames) / nullif(sum(model_frames) filter (where avg_margin is not null), 0), sum(confirms)
   from braille_label_minute where bucket > now() - interval '24 hours' group by 1""")
series = allrows("""select time_bucket_gapfill('1 minute', bucket, now() - interval '60 minutes', now()) b,
     sum(frames), sum(model_frames), approx_percentile(0.5, rollup(model_ms)), approx_percentile(0.95, rollup(model_ms)),
     approx_percentile(0.95, rollup(rtt)), sum(confirms), sum(ambiguous), sum(errors), avg(avg_conf)
   from braille_minute where bucket > now() - interval '60 minutes' and bucket < now() group by 1 order by 1""")
total, first = one("select count(*), min(time) from braille_events where session <> all(%(t)s)")

# ---------- 3. signals ----------
signals = []
def sig(id, level, title, detail, value=None):
    signals.append({"id": id, "level": level, "title": title, "detail": detail, "value": value})
r = recent
idle = not last_frame or (time.time() - last_frame.timestamp()) > 15 * 60
if idle:
    sig("idle", "info", "Detector idle", "No camera frames in the last 15 min. Live signals resume when the camera runs.",
        None if not last_frame else int(last_frame.timestamp() * 1000))
if r["p95"] is not None:
    p95, base = r["p95"], day["p95"]
    lvl = "crit" if p95 > 500 else "warn" if p95 > 250 else "warn" if (base and p95 > 1.5 * base and p95 > 200) else "ok"
    sig("latency", lvl, "Model latency", f"p95 {p95:.0f} ms, median {r['p50']:.0f} ms over 15 min" + (f" (24 h p95 {base:.0f} ms)" if base else "") +
        (". Slow frames delay the 3 s confirmation." if lvl != "ok" else ". CPU is keeping up; a GPU would not change much at this rate."), round(p95))
if r["rtt95"] is not None:
    v = r["rtt95"]
    sig("roundtrip", "crit" if v > 1500 else "warn" if v > 700 else "ok", "Round trip",
        f"p95 {v:.0f} ms from browser to detector and back" + (". Network or upload is the bottleneck, not the model." if v > 700 and r["p95"] and v > 3 * r["p95"] else "."), round(v))
gap = one("""select percentile_cont(0.5) within group (order by g) from (
     select extract(epoch from time - lag(time) over (partition by session order by time)) g from braille_events
     where kind = 'frame' and time > now() - interval '15 minutes' and session <> all(%(t)s)) x where g < 5""")[0]
if gap:
    fps = 1 / gap
    sig("fps", "warn" if fps < 1 else "ok", "Frame rate", f"{fps:.1f} frames/s per camera while active" + (". Below 1 fps the 3 s confirmation sees too few frames to be reliable." if fps < 1 else "."), round(fps, 1))
if r["modelFrames"] >= 30:
    a = r["ambiguous"] / r["modelFrames"]
    sig("ambiguity", "warn" if a > 0.2 else "ok", "Class ambiguity", f"{a:.0%} of model frames had the top two classes within 0.15" +
        (". The model is torn between classes: check lighting, distance and background." if a > 0.2 else "."), round(a, 3))
ab, cf = flick
if ab + cf >= 5:
    fr = ab / (ab + cf)
    sig("flicker", "warn" if fr > 0.6 else "ok", "Label flicker", f"{ab} of {ab + cf} candidates dropped before the {confirm_s:g} s confirmation" +
        (". Labels are unstable; hold objects steadier or closer." if fr > 0.6 else "."), round(fr, 3))
if ttc[0] is not None and ttc[1] >= 3:
    sig("confirm", "warn" if ttc[0] > confirm_s + 2 else "ok", "Time to confirm", f"Median {ttc[0]:.1f} s from first sighting to confirmation (target {confirm_s:g} s, {ttc[1]} confirmations, 24 h)", round(ttc[0], 2))
if servo[1]:
    sig("servo", "warn" if servo[0] > 9 else "ok", "Servo pace", f"{servo[0]:.1f} s per letter on the braille cell across {servo[1]} words (24 h)" +
        (f", {servo[2]} stopped early" if servo[2] else "") + ("." if servo[0] <= 9 else ". Slower than the firmware's ~5 s per letter: check acknowledgments and power."), round(servo[0], 2))
recent_errs = [e for e in errs if time.time() - e[2].timestamp() < 15 * 60]
if errs:
    src, msg, at = errs[0]
    sig("errors", "crit" if len(recent_errs) >= 5 else "warn" if recent_errs else "info", "Errors",
        f"{len(errs)} in 24 h, {len(recent_errs)} in 15 min. Latest ({src}): {msg}", len(errs))
if r["frames"] >= 30:
    w = r["lockedFrames"] / r["frames"]
    if w > 0.5:
        sig("waste", "info", "Uploads while spelling", f"{w:.0%} of frames were sent while a word was being spelled; the model skips them. Pausing capture during playback would save bandwidth.", round(w, 3))
seen = {l[0]: l for l in labels}
missing = [c for c in classes if not (seen.get(c) and seen[c][4])]
if classes and day["frames"]:
    sig("coverage", "info" if missing else "ok", "Class coverage",
        (f"Never confirmed in 24 h: {', '.join(missing)}. Only " + ", ".join(c for c in classes if c not in missing) + " has been exercised." if missing and len(missing) < len(classes)
         else "No class confirmed in 24 h." if missing else "Every class has been confirmed in the last 24 h."), len(classes) - len(missing))

order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
signals.sort(key=lambda s: order[s["level"]])
worst = signals[0]["level"] if signals else "ok"
health_level = "crit" if worst == "crit" else "warn" if worst == "warn" else "ok"

flagged = [s for s in signals if s["level"] in ("warn", "crit")]
if flagged:
    with conn.cursor() as cur:
        cur.executemany("insert into braille_signals (time, signal, level, value, detail) values (now(), %s, %s, %s, %s)",
                        [(s["id"], s["level"], s["value"], s["detail"][:500]) for s in flagged])
history = dict(allrows("select signal, count(*) from braille_signals where time > now() - interval '24 hours' and level in ('warn','crit') group by 1"))
conn.close()

f = lambda v, n=1: None if v is None else round(float(v), n)
now = int(time.time() * 1000)
out = {
    "computedAt": now, "insertedLastRun": inserted, "badLinesLastRun": bad, "health": health_level,
    "totalEvents": total, "firstEventAt": int(first.timestamp() * 1000) if first else None,
    "lastFrameAt": int(last_frame.timestamp() * 1000) if last_frame else None,
    "signals": {f"{i:02d}-{s['id']}": {**s, "seen24h": history.get(s["id"], 0)} for i, s in enumerate(signals)},
    "recent": {k: f(v) for k, v in recent.items()}, "day": {k: f(v) for k, v in day.items()},
    "labels": {l[0]: {"modelFrames": int(l[1] or 0), "avgConf": f(l[2], 3), "avgMargin": f(l[3], 3), "confirms": int(l[4] or 0)} for l in labels},
    "classes": classes or None,
    "series": [{"t": int(s[0].timestamp() * 1000), "frames": int(s[1] or 0), "model": int(s[2] or 0), "p50": f(s[3]), "p95": f(s[4]),
                "rtt95": f(s[5]), "confirms": int(s[6] or 0), "ambiguous": int(s[7] or 0), "errors": int(s[8] or 0), "conf": f(s[9], 3)} for s in series],
    "tiger": {"table": "braille_events", "aggregates": ["braille_minute", "braille_label_minute"], "history": "braille_signals"},
    "metadata": {"type": "entry", "derived": True, "rebuildable": True, "source": "Tiger Data (tiger-sync task)", "updatedAt": now},
}
out = json.loads(json.dumps(out, default=float))
firebase.write(OUT, out)
if (firebase.read(OUT + "/computedAt") or 0) != now:
    raise RuntimeError("insights write to Firebase did not land")
print(f"inserted {inserted} ({bad} bad); {total} events; health {health_level}; " + "; ".join(f"{s['id']}={s['level']}" for s in signals))
