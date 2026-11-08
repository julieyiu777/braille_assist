"""Check pen-region verification and source-safe web-data augmentation."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch

from pen_localizer import verify_proposals
from roi_model import ROIClassifier,preprocess
from train_roi import ROIData


class PenRegionTests(unittest.TestCase):
    def test_proposal_must_be_verified_as_a_pen_and_keeps_detector_bounds(self):
        class Classifier:
            def predict(self,crop):
                self.crop=crop
                return 'pens',np.array([.01,.01,.99])
        classifier=Classifier()
        result=verify_proposals(classifier,np.zeros((100,200,3),np.uint8),[((80,20,100,80),.8)])
        self.assertEqual(result[0],'pens')
        self.assertEqual(result[2],(80,20,100,80))
        self.assertEqual(classifier.crop.shape,(74,34,3))

    def test_low_detector_score_and_non_pen_crop_are_rejected(self):
        class Classifier:
            calls=0
            def predict(self,crop):
                self.calls+=1
                return 'soda',np.array([.01,.99,.01])
        classifier=Classifier();frame=np.zeros((100,200,3),np.uint8)
        self.assertIsNone(verify_proposals(classifier,frame,[((10,10,20,90),.05)]))
        self.assertEqual(classifier.calls,0)
        self.assertIsNone(verify_proposals(classifier,frame,[((10,10,20,90),.8)]))

    def test_global_pen_match_cannot_bypass_disabled_global_fallback(self):
        classifier=ROIClassifier.__new__(ROIClassifier)
        classifier.pen_localizer=SimpleNamespace(proposals=lambda frame:[],confidence=.1)
        classifier.pen_verification_threshold=.95
        classifier.pen_global_fallback_threshold=1.01
        classifier.model=SimpleNamespace(layer4=torch.nn.Identity())
        classifier.predict=lambda frame:('pens',np.array([.01,.01,.999]))
        label,_,bounds=classifier.predict_localized(np.zeros((100,200,3),np.uint8))
        self.assertIsNone(label)
        self.assertIsNone(bounds)

    def test_annotated_full_scene_is_not_cropped_or_translated_out_of_view(self):
        with tempfile.TemporaryDirectory() as directory:
            crop_path=str(Path(directory)/'crop.png');source_path=str(Path(directory)/'source.png')
            Image.new('RGB',(100,100),'blue').save(crop_path)
            source=Image.new('RGB',(200,100),(40,40,40))
            source.paste((0,0,0),(0,0,10,80));source.save(source_path)
            dataset=ROIData([(crop_path,2)],'linear',training=True,
                            training_views={crop_path:dict(original_path=source_path)})
            dataset.jitter=lambda image:image
            with patch('train_roi.random.random',return_value=.1), \
                 patch('train_roi.transforms.RandomAffine',side_effect=AssertionError('Full scene was transformed')):
                tensor,label=dataset[0]
            self.assertEqual(label,2)
            torch.testing.assert_close(tensor,preprocess('linear')(source))


class DatasetPartitionTests(unittest.TestCase):
    def test_source_photographers_do_not_overlap_and_camera_frames_are_excluded(self):
        data=json.loads(Path('data/openimage_pens/split_with_negatives.json').read_text())
        groups={key:set() for key in data['splits']}
        for split,rows in data['splits'].items():
            for path,_ in rows:
                self.assertNotIn('telemetry/frames',path)
                if path in data['source_groups']:
                    groups[split].add(data['source_groups'][path]['split_group'])
        self.assertFalse(groups['train'] & groups['val'])
        self.assertFalse(groups['train'] & groups['test'])
        self.assertFalse(groups['val'] & groups['test'])
        training={path for path,_ in data['splits']['train']}
        self.assertTrue(set(data['training_views']).issubset(training))


if __name__=='__main__':
    unittest.main()
