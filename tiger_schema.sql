-- PostgreSQL base schema; safe to rerun. No hardware actuation occurs in SQL.
CREATE SCHEMA IF NOT EXISTS makecu;
CREATE TABLE IF NOT EXISTS makecu.events (
    observed_at timestamptz NOT NULL,
    event_id uuid NOT NULL,
    session_id uuid NOT NULL,
    device_id text NOT NULL,
    kind text NOT NULL,
    label text,
    description text NOT NULL DEFAULT '',
    severity smallint NOT NULL DEFAULT 0 CHECK (severity BETWEEN 0 AND 3),
    busyness double precision CHECK (busyness BETWEEN 0 AND 1),
    fingerprint text,
    latitude double precision CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision CHECK (longitude BETWEEN -180 AND 180),
    payload jsonb NOT NULL,
    PRIMARY KEY (observed_at, event_id)
);
CREATE INDEX IF NOT EXISTS events_device_time ON makecu.events(device_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS events_kind_time ON makecu.events(kind, observed_at DESC);
CREATE INDEX IF NOT EXISTS events_search ON makecu.events USING GIN
    (to_tsvector('english', coalesce(label, '') || ' ' || description));
CREATE TABLE IF NOT EXISTS makecu.alerts (
    event_id uuid PRIMARY KEY,
    observed_at timestamptz NOT NULL,
    device_id text NOT NULL,
    severity smallint NOT NULL,
    acknowledged_at timestamptz,
    payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_pending ON makecu.alerts(device_id, observed_at)
    WHERE acknowledged_at IS NULL;
CREATE OR REPLACE FUNCTION makecu.notify_urgent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.severity >= 2 THEN
        INSERT INTO makecu.alerts(event_id, observed_at, device_id, severity, payload)
        VALUES (NEW.event_id, NEW.observed_at, NEW.device_id, NEW.severity,
                jsonb_build_object('event_id', NEW.event_id, 'kind', NEW.kind,
                                   'description', NEW.description, 'payload', NEW.payload))
        ON CONFLICT DO NOTHING;
        -- A wake-up signal only: pending alerts remain durable until acknowledged.
        PERFORM pg_notify('makecu_alerts', json_build_object('event_id', NEW.event_id,
                                                          'device_id', NEW.device_id)::text);
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS notify_urgent ON makecu.events;
CREATE TRIGGER notify_urgent AFTER INSERT ON makecu.events
    FOR EACH ROW EXECUTE FUNCTION makecu.notify_urgent();

CREATE OR REPLACE VIEW makecu.minute_activity AS
SELECT device_id, date_trunc('minute', observed_at) AS minute,
       count(*) FILTER (WHERE kind='detection') AS captured_frames,
       avg(busyness) FILTER (WHERE kind='detection') AS mean_busyness,
       count(*) FILTER (WHERE severity >= 2) AS urgent_events
FROM makecu.events GROUP BY device_id, date_trunc('minute', observed_at);

CREATE OR REPLACE VIEW makecu.servo_health AS
SELECT device_id, payload->>'pin' AS pin, count(*) AS samples,
       avg((payload->>'duration_ms')::double precision) AS mean_ms,
       percentile_cont(.95) WITHIN GROUP (ORDER BY (payload->>'duration_ms')::double precision) AS p95_ms,
       max(observed_at) AS last_seen
FROM makecu.events WHERE kind='servo' GROUP BY device_id, payload->>'pin';

CREATE OR REPLACE VIEW makecu.reading_speed AS
SELECT device_id, payload->>'user_id' AS user_id, count(*) AS samples,
       sum((payload->>'cells')::double precision)/sum((payload->>'seconds')::double precision) AS cells_per_second
FROM makecu.events WHERE kind='reading' AND (payload->>'correct')::boolean
GROUP BY device_id, payload->>'user_id';

CREATE OR REPLACE VIEW makecu.hazard_map AS
SELECT device_id, label, round(latitude::numeric,4) AS latitude_cell,
       round(longitude::numeric,4) AS longitude_cell,
       avg(latitude) AS latitude, avg(longitude) AS longitude,
       count(*) AS sightings, count(DISTINCT session_id) AS sessions,
       max(severity) AS severity, max(observed_at) AS last_seen
FROM makecu.events WHERE kind='hazard' AND latitude IS NOT NULL AND longitude IS NOT NULL
GROUP BY device_id, label, round(latitude::numeric,4), round(longitude::numeric,4);
