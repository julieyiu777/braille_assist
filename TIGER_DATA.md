Tiger Data collection is integrated into `detect.py`. It saves a timestamped
record and an unannotated JPEG for every frame returned by the camera loop,
including the 100-second detection lock. Classification still runs only while
unlocked. Held labels are marked `inference_fresh=false` and retain the timestamp
of the last actual inference. Logs never pretend a held label is a new detection.

The current classifier recognizes chips, soda, and pens. This work adds a data
collection/feedback system; it does not add recognition of mugs, room semantics,
stairs, traffic, or construction. Those require appropriate models or human
annotations. An optional [ESP32 USB bridge](BRAILLE_OUTPUT.md) now sends each
confirmed label character by character to the supplied six-servo firmware.
Its command acknowledgments do not provide measured physical pin positions.

| Requested aspect | What was added | What still needs a real input/connection |
| --- | --- | --- |
| Detection log | UTC time, capture/session/device IDs, raw frame path, all class scores, bounds, model SHA-256, ROI, confirmation/lock/freshness, location when supplied. Durable local SQLite outbox and asynchronous idempotent PostgreSQL upload. | Tiger Data URL and schema initialization for cloud upload. Raw frames stay on this computer. |
| Busy-ness | Smoothed motion/edge-density score, visible calm/busy indicator, delivery plans that defer low-priority detail during activity and preserve urgent messages. | This is a heuristic needing camera calibration. It plans output; a pin driver must consume plans. |
| Servo health | Measured per-pin movement durations, baseline comparison, slow-pin event escalation; SQL mean and p95 health view. | Controller completion acknowledgments. Measurements are never invented from camera frames. |
| Reading speed | Correct-read cells/second from actual feedback, per-user pacing, suggested message dwell time. | Reader completion feedback and a Braille encoder; character counts are only a cell-count estimate. The detector’s requested 3-second confirmation and 100-second lock stay unchanged. |
| Scene memory | Persistent perceptual fingerprints with change-only label summaries across sessions. | Fingerprints are approximate view matches, not proof that a room is the same. Only current model labels are summarized. |
| Past search | Device-scoped local text search and PostgreSQL full-text index/search. Cached labels are excluded from search results. Human descriptions are searchable too. | “Red mug” is searchable only if a future model or human actually recorded it. |
| Compression | TimescaleDB hypertable and automatic Hypercore columnstore policy after seven days; storage command reports real before/after byte statistics. | Requires a running TimescaleDB service. No compression percentage or savings are claimed before actual chunks convert. JPEG files are outside Hypercore and are not deleted automatically. |
| Replay | Run any compatible classifier against archived raw frames, preserving source times and old model provenance. Reports disagreements separately from correctness against human annotations. | Raw archive must be available on the replay machine. Unlabeled replay cannot establish accuracy. |
| Instant alerts | Transactional urgent-event trigger, `LISTEN/NOTIFY`, durable pending-alert table, explicit acknowledgments, restart recovery. No separate message broker. | Database connection and a controller consumer. Notifications arrive after commit and upload; outages delay cloud delivery. This is not a safety-certified navigation system. |
| Hazard map | Human/externally reported location-tagged hazards grouped by location and distinct sessions; nearby lookup using distance; startup warning at supplied camera location. | GPS/position updates and a real hazard detector or human reports. Product detections are never automatically declared hazards. |

Run locally without connecting to Tiger Data:

```bash
.venv/bin/python detect.py
.venv/bin/python tiger_cli.py status
.venv/bin/python tiger_cli.py search "where was the pens"
```

Local data is under `runs/telemetry/events.sqlite3` and
`runs/telemetry/frames/<session-id>/`. The session ID is printed to stderr when
the camera starts. Frame archives are saved before drawing boxes or labels.
They use JPEG quality 90, so “raw” means unannotated, not sensor-lossless. The
capture loop can run more slowly because of classification and disk writes;
this records captured frames, not frames dropped by the camera driver.
`--metadata-only` disables image archiving and therefore image replay;
Local collection remains enabled by default. `--device-id` separates devices and
`--user-id` selects a reading profile. Use `--location LATITUDE LONGITUDE` only
when a genuine position is available; it is a static supplied position, not GPS
tracking. No real camera session was started automatically during implementation.

Connect Tiger Data:

1. Install dependencies with `.venv/bin/python -m pip install -r requirements.txt`.
2. Set `TIGER_DATABASE_URL` in the environment, or copy `.env.example` to `.env`
   and replace its placeholder with your connection URI. `.env` is ignored by
   Git. Connection strings are never printed by application error handlers.
3. Run `.venv/bin/python tiger_cli.py init`. This applies `tiger_schema.sql`
   and `tiger_timescale.sql` for TimescaleDB >= 2.18. It adds tables, indexes,
   views, and the policy in the `makecu` schema. Use an account allowed to create
   those objects. For plain PostgreSQL testing, use `init --postgres-only`.
4. Run `.venv/bin/python tiger_cli.py sync` to upload the existing outbox.
   Camera sessions launched with `--tiger-data` upload in the background. Failed inserts remain
   pending; a retry after a committed upload is safe because event IDs/times
   are stable and inserts use `ON CONFLICT DO NOTHING`.
5. `.venv/bin/python tiger_cli.py storage` reports the TimescaleDB
   `before_compression_total_bytes` / `after_compression_total_bytes` fields.
   A new or uncompressed service has no useful before/after savings yet.

Tiger Data is disabled by default, even when a URL is saved in `.env`. Enable
it explicitly with `python detect.py --tiger-data`. That mode requires a
configured URL, a reachable database, a readable events schema, INSERT permission,
and a successful initial upload. An upload failure stops detection; pending events remain
in the local outbox. Restore the connection and restart, or use `tiger_cli.py sync`
to upload pending events. The CLI never
silently substitutes another cloud database. Database notifications and views
can be inspected from the Tiger Data SQL editor:

```sql
SELECT * FROM makecu.minute_activity ORDER BY minute DESC;
SELECT * FROM makecu.servo_health;
SELECT * FROM makecu.reading_speed;
SELECT * FROM makecu.hazard_map ORDER BY last_seen DESC;
SELECT * FROM hypertable_columnstore_stats('makecu.events');
```

Improve actual camera training data:

```bash
.venv/bin/python tiger_cli.py review-export --output data/camera_review --limit 100
.venv/bin/python tiger_cli.py annotate --event-id CAPTURE_UUID --label negative --description "black remote wrongly detected as a pen"
.venv/bin/python tiger_cli.py export-labels --output data/camera_verified
.venv/bin/python tiger_cli.py replay --session SESSION_UUID --model runs/rollerball_active/best.pt --output runs/replays/comparison.json
```

Review export samples uncertain fresh frames and confirmed high-confidence
predictions (to catch confident false matches), with fingerprint deduplication.
Reviewing/editing `review.json` alone does not label the database; use `annotate`
for each verified label. Training export includes only human-verified labels,
retains their capture-session grouping, and uses the most recent correction.
Before training, split by independent capture session, keeping adjacent frames
in the same partition. Do not randomly split these exported JPEGs with the old
web-image splitter. Model predictions are never silently used as ground truth.

Hardware and human feedback examples (replace values with actual measurements):

```bash
.venv/bin/python tiger_cli.py servo --pin 1 --duration-ms 120 --target raised
.venv/bin/python tiger_cli.py reading --user-id default --cells 40 --seconds 20
.venv/bin/python tiger_cli.py hazard --label obstruction --description "Human reported blocked walkway" --location 40.0 -74.0 --severity 2
.venv/bin/python tiger_cli.py nearby --location 40.0 -74.0 --radius-m 50
.venv/bin/python tiger_cli.py search "where was the red mug" --cloud
.venv/bin/python tiger_cli.py listen
.venv/bin/python tiger_cli.py ack-alert --event-id ALERT_UUID
```

`servo` records a supplied measurement; it does not move a pin. A controller can
call `EventStore.record_servo()` after actual movement completes. CLI measurements
and API hardware samples keep separate source labels for baseline comparison.
`listen` prints pending alerts as JSON and waits for database wake-ups. It never
acknowledges them automatically or actuates hardware. A real consumer must
deduplicate `event_id`, perform its action, and acknowledge only after completion.
On reconnection, rerun the listener to recover unacknowledged alerts; delivery
is at least once, not exactly once. Urgent plans bypass busy-scene suppression.
Slow-pin alerts have severity 2; a real controller still needs a defined recovery
policy rather than continuing to drive a failing actuator.

Validation:

```bash
.venv/bin/python -m unittest test_detection test_tiger_data
```

Local tests exercise archival during locks, raw-image ordering, persistence,
busy-policy priority, servo slowdown, measured reading rates, scene changes,
device-scoped search, hazard recurrence/distance, human-only training export,
timestamped model replay, and preserving pending data when cloud sync fails.
Cloud schema, compression, and notifications require a real connection before
their end-to-end behavior can be verified.

Five further integration tests run against an isolated local PostgreSQL server,
never the user's Tiger Data connection. They verify real schema initialization,
committed uploads and duplicate retries, refusing early local acknowledgments
inside an uncommitted transaction, full-text/device isolation, all feedback
views, and post-commit notifications with durable alerts:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest test_tiger_postgres
```

The test server uses a temporary data directory and shuts down after the tests.
This validates PostgreSQL behavior, not the TimescaleDB extension or hardware.
At implementation time, 32 camera/local checks and all 5 PostgreSQL integration
tests passed. A Tiger Data connection had not yet been configured, so hosted
initialization and the seven-day columnstore policy remain unverified.

Official references:

- [Tiger Data hypertables](https://www.tigerdata.com/docs/reference/timescaledb/hypertables/create_table)
- [Columnstore policy](https://www.tigerdata.com/docs/reference/timescaledb/hypercore/add_columnstore_policy)
- [Actual compression statistics](https://www.tigerdata.com/docs/reference/timescaledb/hypercore/hypertable_columnstore_stats)
- [PostgreSQL notifications and commit semantics](https://www.postgresql.org/docs/current/sql-notify.html)

The supplied `mylighthouse.com` link is a separate hotel analytics product, not
the Tiger Data service used by this implementation.
