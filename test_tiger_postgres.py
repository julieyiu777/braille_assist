"""Real PostgreSQL integration tests using an isolated, temporary local server.

Never connects to the user's TIGER_DATABASE_URL. TimescaleDB policies require
their own hosted/self-hosted TimescaleDB validation.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from tiger_data import EventStore, ROOT, sync_pending
from tiger_cli import cloud_search


@unittest.skipUnless(importlib.util.find_spec('pgserver'), 'Install requirements-dev.txt to run PostgreSQL integration tests')
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pgserver
        import psycopg
        cls.directory = tempfile.TemporaryDirectory(prefix='makecu-pg-')
        cls.root = Path(cls.directory.name)
        cls.server = pgserver.get_server(cls.root/'pgdata', cleanup_mode='stop')
        cls.uri = cls.server.get_uri()
        with psycopg.connect(cls.uri) as connection:
            connection.execute((ROOT/'tiger_schema.sql').read_text())
        # Applying the migration again preserves rows and compiles all views.
        with psycopg.connect(cls.uri) as connection:
            connection.execute((ROOT/'tiger_schema.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.server.cleanup()
        cls.directory.cleanup()

    def setUp(self):
        import psycopg
        self.device = 'test-'+str(uuid.uuid4())
        self.store = EventStore(self.root/(self.device+'.sqlite3'), device_id=self.device)
        self.connection = psycopg.connect(self.uri)

    def tearDown(self):
        self.connection.close()
        self.store.close()

    def test_real_upload_retry_is_idempotent_after_lost_local_ack(self):
        identity = self.store.record('annotation', label='negative', description='red mug on the desk')
        self.assertEqual(sync_pending(self.store,self.connection),1)
        # Simulate a crash after server commit, before the local acknowledgment.
        self.store.db.execute('UPDATE events SET synced=0 WHERE event_id=?',(identity,))
        self.store.db.commit()
        self.assertEqual(sync_pending(self.store,self.connection),1)
        count = self.connection.execute('SELECT count(*) FROM makecu.events WHERE event_id=%s',(identity,)).fetchone()[0]
        self.assertEqual(count,1)

    def test_open_remote_transaction_cannot_ack_local_events_early(self):
        self.store.record('annotation',description='pending')
        self.connection.execute('SELECT 1')
        with self.assertRaises(RuntimeError):
            sync_pending(self.store,self.connection)
        self.assertEqual(self.store.db.execute('SELECT synced FROM events').fetchone()[0],0)
        self.connection.rollback()
        self.assertEqual(sync_pending(self.store,self.connection),1)

    def test_urgent_trigger_notifies_after_commit_and_keeps_durable_alert(self):
        import psycopg
        with psycopg.connect(self.uri,autocommit=True) as listener:
            listener.execute('LISTEN makecu_alerts')
            identity = self.store.record('hazard',label='test obstruction',severity=2,
                                        description='Integration test only',location=(40.,-74.))
            sync_pending(self.store,self.connection)
            notifications = list(listener.notifies(timeout=2,stop_after=1))
            self.assertEqual(len(notifications),1)
            self.assertEqual(json.loads(notifications[0].payload)['event_id'],identity)
        # A disconnected listener can recover this pending row later.
        row = self.connection.execute('SELECT acknowledged_at,device_id FROM makecu.alerts WHERE event_id=%s',(identity,)).fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(row[1],self.device)
        self.connection.commit()
        self.store.db.execute('UPDATE events SET synced=0 WHERE event_id=?',(identity,))
        self.store.db.commit()
        sync_pending(self.store,self.connection)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM makecu.alerts WHERE event_id=%s',(identity,)).fetchone()[0],1)

    def test_full_text_search_and_device_boundary(self):
        self.store.record('annotation',label='negative',description='red mug on desk')
        self.store.record('annotation',label='negative',description='blue mug in kitchen')
        sync_pending(self.store,self.connection)
        results = cloud_search(self.connection,'where was the red mug',self.device)
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['description'],'red mug on desk')
        self.assertEqual(cloud_search(self.connection,'red mug','another-device'),[])

    def test_health_reading_and_hazard_views_use_real_rows(self):
        self.store.record_servo('1',100,'raised')
        self.store.record_reading('reader',40,20)
        self.store.record('hazard',label='obstruction',severity=2,location=(40.,-74.))
        sync_pending(self.store,self.connection)
        mean = self.connection.execute('SELECT mean_ms FROM makecu.servo_health WHERE device_id=%s',(self.device,)).fetchone()[0]
        rate = self.connection.execute('SELECT cells_per_second FROM makecu.reading_speed WHERE device_id=%s',(self.device,)).fetchone()[0]
        sightings = self.connection.execute('SELECT sightings FROM makecu.hazard_map WHERE device_id=%s',(self.device,)).fetchone()[0]
        self.assertEqual(mean,100)
        self.assertEqual(rate,2)
        self.assertEqual(sightings,1)


if __name__ == '__main__':
    unittest.main()
