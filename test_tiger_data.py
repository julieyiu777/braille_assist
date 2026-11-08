"""Check camera collection, genuine feedback, outage recovery, and model replay."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import contextlib
import io

import cv2
import numpy as np

from tiger_data import (CaptureLog, EventStore, SceneMeter, delivery_plan, fingerprint,
                        hash_distance, sync_pending)
from tiger_cli import annotate, export_labels, replay, review_export


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = EventStore(self.root/'events.sqlite3')
        self.model = self.root/'model.pt'
        self.model.write_bytes(b'test checkpoint identity')
        self.frame = np.zeros((100, 200, 3), dtype=np.uint8)
        self.frame[20:80, 80:100] = 255

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def capture(self, **kwargs):
        log = CaptureLog(self.store, self.model, (0, 0, 1, 1))
        log.record_frame(self.frame, 'pens', [.01, .01, .99], (80, 20, 100, 80),
                         fresh=True, confirmed=True, locked=True, **kwargs)
        return self.store.rows('detection')[0]

    def test_archive_is_raw_and_lock_frames_are_not_fresh_predictions(self):
        original = self.frame.copy()
        log = CaptureLog(self.store, self.model, (0, 0, 1, 1))
        log.record_frame(self.frame, 'pens', [.01,.01,.99], (80,20,100,80),
                         fresh=True, confirmed=True, locked=True, captured_at='2026-11-08T10:00:00+00:00')
        self.frame[:] = 30
        log.record_frame(self.frame, 'pens', [.01,.01,.99], (80,20,100,80),
                         fresh=False, confirmed=False, locked=True, captured_at='2026-11-08T10:00:01+00:00')
        rows = sorted(self.store.rows('detection'), key=lambda row: row['observed_at'])
        first, second = [json.loads(row['payload']) for row in rows]
        self.assertTrue(first['inference_fresh'])
        self.assertFalse(second['inference_fresh'])
        self.assertEqual(first['inference_at'], second['inference_at'])
        self.assertNotEqual(first['frame_path'], second['frame_path'])
        saved = cv2.imread(first['frame_path'])
        self.assertLess(float(np.abs(saved.astype(float)-original).mean()), 3)
        self.assertEqual(len(self.store.rows('output_plan')), 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events WHERE synced=0').fetchone()[0], 4)

    def test_metadata_only_has_no_replay_frame(self):
        log = CaptureLog(self.store, self.model, (0,0,1,1), save_frames=False)
        log.record_frame(self.frame, None, [.1,.1,.1], None, fresh=True, confirmed=False, locked=False)
        self.assertIsNone(json.loads(self.store.rows('detection')[0]['payload'])['frame_path'])

    def test_scene_memory_survives_restart_and_suppresses_unchanged_output(self):
        hashed = fingerprint(self.frame)
        first = self.store.scene_changes(hashed, ['pens'])
        self.assertFalse(first['seen_before'])
        self.store.close()
        self.store = EventStore(self.root/'events.sqlite3')
        second = self.store.scene_changes(hashed, ['pens'])
        self.assertTrue(second['seen_before'])
        self.assertEqual(second['added'], [])
        changed = self.store.scene_changes(hashed, ['soda'])
        self.assertEqual(changed['added'], ['soda'])
        self.assertEqual(changed['removed'], ['pens'])
        self.capture()
        plan = json.loads(self.store.rows('output_plan')[0]['payload'])['plan']
        self.assertTrue(plan['allowed'])
        self.capture()
        plans = [json.loads(row['payload'])['plan'] for row in self.store.rows('output_plan')]
        self.assertIn(False, [plan['allowed'] for plan in plans])

    def test_busy_policy_preserves_urgent_messages(self):
        self.assertFalse(delivery_plan('pens', 0, .9)['allowed'])
        self.assertTrue(delivery_plan('urgent obstruction', 3, .9)['allowed'])
        self.assertTrue(delivery_plan('pens', 0, .1)['allowed'])
        self.assertFalse(delivery_plan('warning', 2, .9)['actuated'])

    def test_scene_meter_tracks_motion_without_claiming_hazards(self):
        meter = SceneMeter()
        still = [meter.update(np.zeros((100,100,3),np.uint8)) for _ in range(5)]
        self.assertEqual(max(still), 0)
        moving = []
        for i in range(8):
            moving.append(meter.update(np.full((100,100,3),255 if i%2==0 else 0,np.uint8)))
        self.assertGreater(max(moving), .65)
        self.assertEqual(hash_distance(fingerprint(self.frame), fingerprint(self.frame)), 0)

    def test_servo_slowing_uses_measured_pin_baseline(self):
        for _ in range(6):
            self.store.record_servo('1', 100, 'raised')
        identity = self.store.record_servo('1', 180, 'raised')
        row = self.store.db.execute('SELECT * FROM events WHERE event_id=?', (identity,)).fetchone()
        self.assertEqual(row['severity'], 2)
        self.assertTrue(json.loads(row['payload'])['slow'])
        self.store.record_servo('2', 500, 'raised')
        row = next(row for row in self.store.rows('servo') if json.loads(row['payload'])['pin']=='2')
        self.assertEqual(row['severity'], 0)

    def test_reading_feedback_changes_suggested_dwell_not_detection_lock(self):
        self.store.record_reading('reader', 40, 20)
        self.store.record_reading('reader', 20, 20)
        self.store.record_reading('reader', 1000, 1, correct=False)
        rate = self.store.reading_rate('reader')
        self.assertEqual(rate, 1.5)
        self.assertEqual(delivery_plan('123456',0,.1,rate)['suggested_seconds'], 4)
        self.assertEqual(self.store.reading_rate('other'), 2)

    def test_search_descriptions_and_device_isolation(self):
        self.store.record('annotation', label='negative', description='red mug on desk')
        self.store.record('annotation', label='negative', description='blue mug in kitchen')
        other = EventStore(self.store.path, device_id='other-camera')
        other.record('annotation', description='red mug private room')
        other.close()
        results = self.store.search('where was the red mug?')
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['description'], 'red mug on desk')
        self.assertEqual(self.store.search('the where was'), [])

    def test_hazard_map_groups_recurring_reports_and_checks_distance(self):
        self.store.record('hazard', label='construction', severity=2, location=(40.,-74.))
        other = EventStore(self.store.path)
        other.record('hazard', label='construction', severity=2, location=(40.,-74.))
        other.close()
        nearby = self.store.nearby_hazards(40.,-74.)
        self.assertEqual(len(nearby),1)
        self.assertEqual(nearby[0]['sightings'],2)
        self.assertEqual(nearby[0]['sessions'],2)
        self.assertEqual(self.store.nearby_hazards(42.,-74.), [])
        self.assertEqual(self.store.rows('detection'), [])

    def test_only_human_annotations_are_exported_for_training(self):
        row = self.capture()
        result = export_labels(self.store, self.root/'labels')
        self.assertEqual(result['exported'],0)
        annotate(self.store, row['event_id'], 'negative', 'black remote mistaken for a pen')
        result = export_labels(self.store, self.root/'labels')
        self.assertEqual(result['exported'],1)
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual(manifest[0]['label'],'negative')
        self.assertEqual(manifest[0]['split_group'],row['session_id'])
        self.assertEqual(len(self.store.search('remote')),1)

    def test_review_includes_confident_confirmations_to_find_false_positives(self):
        row = self.capture()
        report = review_export(self.store, self.root/'review',10)
        self.assertEqual(report['exported'],1)
        manifest = json.loads(Path(report['manifest']).read_text())
        self.assertEqual(manifest[0]['event_id'],row['event_id'])
        self.assertIsNone(manifest[0]['human_label'])

    def test_replay_keeps_original_times_and_does_not_call_disagreement_accuracy(self):
        row = self.capture(captured_at='2026-11-08T10:00:00+00:00')
        class FakeClassifier:
            def predict_localized(self, frame):
                return 'soda', np.array([.01,.99,.01]), (10,10,50,90)
        with patch('roi_model.ROIClassifier', return_value=FakeClassifier()):
            report = replay(self.store,self.store.session_id,self.model,self.root/'replay.json')
        self.assertEqual(report['replayed'],1)
        self.assertEqual(report['label_changes'],1)
        self.assertIsNone(report['accuracy_vs_human'])
        original = json.loads((self.root/'replay.json').read_text())['frames'][0]
        self.assertEqual(original['original_time'],row['observed_at'])
        annotate(self.store,row['event_id'],'soda','human checked soda')
        with patch('roi_model.ROIClassifier', return_value=FakeClassifier()):
            report = replay(self.store,self.store.session_id,self.model,self.root/'replay.json')
        self.assertEqual(report['accuracy_vs_human'],1)

    def test_record_rejects_invalid_measurements(self):
        with self.assertRaises(ValueError):
            self.store.record('detection', observed_at='2026-11-08T10:00:00')
        with self.assertRaises(ValueError):
            self.store.record('hazard', location=(100.,10.))
        with self.assertRaises(ValueError):
            self.store.record_servo('1',float('nan'),'raised')
        with self.assertRaises(ValueError):
            self.store.record_reading('reader',40,0)


class SyncRecoveryTests(unittest.TestCase):
    def test_commit_failure_leaves_durable_outbox_for_retry(self):
        class BrokenTransaction:
            def __enter__(self):
                raise OSError('offline')
            def __exit__(self,*args):
                pass
        class Offline:
            from types import SimpleNamespace
            from psycopg.pq import TransactionStatus
            info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
            def transaction(self):
                return BrokenTransaction()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'events.sqlite3'
            store = EventStore(path)
            identity = store.record('hazard',severity=2,location=(40.,-74.))
            with self.assertRaises(OSError):
                sync_pending(store,Offline())
            store.close()
            store = EventStore(path)
            row = store.db.execute('SELECT * FROM events WHERE event_id=?',(identity,)).fetchone()
            self.assertEqual(row['synced'],0)
            self.assertEqual(row['severity'],2)
            store.close()


class CameraLoopTests(unittest.TestCase):
    def test_real_loop_collects_during_lock_and_resumes_inference_after_ten_seconds(self):
        import roi_detect
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root/'model.pt'
            checkpoint.write_bytes(b'test checkpoint identity')
            clock = [0.,1.,3.1,4.,5.,13.1,14.,17.2]
            state = {'index': -1, 'calls': 0}
            class Camera:
                def isOpened(self):
                    return True
                def read(self):
                    state['index'] += 1
                    if state['index'] == len(clock):
                        return False, None
                    return True, np.full((240,320,3),state['index'],np.uint8)
                def release(self):
                    pass
            class Classifier:
                def predict_localized(self, frame):
                    state['calls'] += 1
                    label = 'pens' if state['index'] < 3 else 'soda'
                    scores = np.array([.01,.01,.99]) if label=='pens' else np.array([.01,.99,.01])
                    return label,scores,(40,40,80,200)
            class NoSync:
                def __init__(self,*args):
                    pass
                def close(self):
                    pass
            args = ['detect.py','--classifier',str(checkpoint),'--telemetry-db',str(root/'events.sqlite3')]
            with patch('sys.argv',args), patch('roi_detect.ROIClassifier',return_value=Classifier()), \
                 patch('roi_detect.cv2.VideoCapture',return_value=Camera()), \
                 patch('roi_detect.time.monotonic',side_effect=lambda: clock[state['index']]), \
                 patch('roi_detect.cv2.imshow'), patch('roi_detect.cv2.waitKey',return_value=-1), \
                 patch('roi_detect.cv2.getWindowProperty',return_value=1), \
                 patch('roi_detect.cv2.destroyAllWindows'), patch('roi_detect.TigerSync',NoSync), \
                 contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
                roi_detect.main()
            self.assertEqual(output.getvalue().splitlines(), ['pens','soda'])
            self.assertEqual(state['calls'],6)
            store = EventStore(root/'events.sqlite3')
            rows = sorted(store.rows('detection'),key=lambda row: json.loads(row['payload'])['sequence'])
            self.assertEqual(len(rows),8)
            payloads = [json.loads(row['payload']) for row in rows]
            self.assertEqual([value['inference_fresh'] for value in payloads],
                             [True,True,True,False,False,True,True,True])
            self.assertEqual(sum(value['confirmed'] for value in payloads),2)
            self.assertEqual(len({value['frame_path'] for value in payloads}),8)
            # Raw frames were archived before rectangles/text were painted.
            for i,payload in enumerate(payloads):
                self.assertEqual(float(cv2.imread(payload['frame_path']).mean()), i)
            store.close()


if __name__ == '__main__':
    unittest.main()
