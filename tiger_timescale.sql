-- Tiger Data / TimescaleDB >= 2.18, run AFTER tiger_schema.sql.
-- Extension must already be available on the service.
CREATE EXTENSION IF NOT EXISTS timescaledb;
SELECT create_hypertable('makecu.events', by_range('observed_at'), if_not_exists => true,
                        migrate_data => true);
ALTER TABLE makecu.events SET (
    timescaledb.enable_columnstore = true,
    timescaledb.segmentby = 'device_id,kind',
    timescaledb.orderby = 'observed_at DESC'
);
CALL add_columnstore_policy('makecu.events', after => INTERVAL '7 days', if_not_exists => true);
