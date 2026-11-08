"""Check region isolation, rejection, negative loss, and stable output."""
import unittest

import numpy as np
import torch
from PIL import Image, ImageOps

from roi_detect import DetectionConfirmation, StableOutput, detect_product, predict_roi, roi_bounds
from roi_model import activation_bounds, accepted_label, preprocess, rejection_loss


class ClassifierStub:
    def predict(self,crop):
        self.crop = crop.copy()
        return "chips",np.array([.99,.01,.01])


class PlacementTests(unittest.TestCase):
    def test_confirmation_requires_more_than_three_seconds_then_locks(self):
        state = DetectionConfirmation()
        scores = np.array([.99, .01, .01])
        bounds = (10, 20, 80, 90)
        self.assertFalse(state.observe("chips", scores, bounds, 100))
        self.assertEqual(state.timer_text(101), "Confirming: 2.0s")
        self.assertFalse(state.observe("chips", scores, bounds, 103))
        self.assertTrue(state.observe("chips", scores, bounds, 103.1))
        self.assertEqual(state.timer_text(103.1), "Locked: 100.0s")
        scores[0] = .1
        self.assertFalse(state.observe("soda", scores, (0, 0, 5, 5), 108))
        self.assertEqual(state.detection[0], "chips")
        self.assertEqual(state.detection[1][0], .99)
        self.assertEqual(state.detection[2], bounds)
        self.assertTrue(state.locked(113.1))
        self.assertEqual(state.timer_text(113.1), "Locked: 90.0s")
        self.assertTrue(state.locked(203))
        self.assertFalse(state.locked(203.1))
        self.assertEqual(state.timer_text(203.1), "Scanning...")
        # A fresh three-second confirmation is required after every lock.
        self.assertFalse(state.observe("chips", scores, bounds, 203.1))
        self.assertTrue(state.observe("chips", scores, bounds, 206.2))

    def test_label_change_absence_and_missing_box_restart_confirmation(self):
        scores = np.array([.99, .01, .01])
        bounds = (0, 0, 10, 10)
        for label, box in (("soda", bounds), (None, None), ("chips", None)):
            with self.subTest(label=label, bounds=box):
                state = DetectionConfirmation()
                state.observe("chips", scores, bounds, 0)
                state.observe(label, scores, box, 2.9)
                self.assertFalse(state.observe("chips", scores, bounds, 3.1))
                self.assertFalse(state.locked(3.1))
                self.assertTrue(state.observe("chips", scores, bounds, 6.2))

    def test_confirmation_respects_additional_frame_requirement(self):
        state = DetectionConfirmation(stable_frames=3)
        scores = np.array([.99, .01, .01])
        bounds = (0, 0, 10, 10)
        self.assertFalse(state.observe("chips", scores, bounds, 0))
        self.assertFalse(state.observe("chips", scores, bounds, 4))
        self.assertTrue(state.observe("chips", scores, bounds, 4.1))

    def test_activation_box_follows_peak_and_ignores_separate_noise(self):
        activation = np.zeros((100, 200), dtype=np.float32)
        activation[20:70, 50:90] = 1
        activation[80:90, 180:190] = .5
        self.assertEqual(activation_bounds(activation, (100, 200, 3)), (47, 17, 93, 73))
        self.assertIsNone(activation_bounds(np.zeros((3, 3), dtype=np.float32), (100, 200, 3)))

    def test_localized_box_is_translated_out_of_roi(self):
        class LocalizedStub:
            def predict_localized(self, crop):
                self_shape = crop.shape
                assert self_shape == (50, 90, 3)
                return "chips", np.array([.99, .01]), (10, 5, 60, 40)
        label, _, bounds = detect_product(LocalizedStub(), np.zeros((100, 200, 3), dtype=np.uint8),
                                         (0, .5, .45, 1))
        self.assertEqual(label, "chips")
        self.assertEqual(bounds, (10, 55, 60, 90))

    def test_full_screen_includes_every_pixel(self):
        frame = np.arange(100*200*3,dtype=np.uint8).reshape(100,200,3)
        classifier = ClassifierStub()
        _,_,bounds = predict_roi(classifier,frame,(0,0,1,1))
        self.assertEqual(bounds,(0,0,200,100))
        np.testing.assert_array_equal(classifier.crop,frame)

    def test_only_bottom_left_pixels_reach_classifier(self):
        frame = np.full((100,200,3),255,dtype=np.uint8)
        frame[50:100,:90] = 17
        classifier = ClassifierStub()
        predict_roi(classifier,frame,(0,.5,.45,1))
        self.assertEqual(classifier.crop.shape,(50,90,3))
        self.assertTrue(np.all(classifier.crop == 17))
        frame[:50] = 0
        frame[:,90:] = 0
        predict_roi(classifier,frame,(0,.5,.45,1))
        self.assertTrue(np.all(classifier.crop == 17))

    def test_invalid_roi_rejected(self):
        for roi in ((.5,0,.4,1),(-.1,0,.5,1),(0,0,1,2)):
            with self.assertRaises(ValueError):
                roi_bounds((100,100,3),roi)

    def test_catalog_and_placement_preserve_product_scale(self):
        image = Image.new("RGB",(100,200),(20,80,220))
        image.paste((240,20,50),(20,40,80,160))
        placed = ImageOps.pad(image,(576,360),color=(128,128,128))
        transform = preprocess("cnn")
        difference = (transform(image)-transform(placed)).abs().mean().item()
        self.assertLess(difference,.025)

    def test_reject_unclear_or_non_target(self):
        self.assertIsNone(accepted_label([.1,.1,.1]))
        self.assertIsNone(accepted_label([.95,.91,.1]))
        self.assertIsNone(accepted_label([.8,.1,.1]))
        self.assertEqual(accepted_label([.99,.1,.2]),"chips")
        self.assertEqual(accepted_label([.1,.99,.2]),"soda")
        self.assertEqual(accepted_label([.1,.2,.99]),"pens")

    def test_pen_threshold_rejects_false_matches_without_raising_other_thresholds(self):
        thresholds = [.75, .75, .98]
        self.assertIsNone(accepted_label([.1, .1, .95], thresholds, .25))
        self.assertEqual(accepted_label([.85, .1, .1], thresholds, .25), "chips")
        self.assertEqual(accepted_label([.1, .85, .1], thresholds, .25), "soda")
        self.assertEqual(accepted_label([.1, .1, .99], thresholds, .25), "pens")
        self.assertIsNone(accepted_label([.9, .1, .99], thresholds, .25))

    def test_calibration_raises_pen_cutoff_only_as_far_as_validation_needs(self):
        from train_roi import calibrate
        scores = np.array([[.99, .01, .01], [.01, .99, .01],
                           [.01, .01, .99], [.01, .01, .88]])
        _, _, thresholds, _, report = calibrate(scores, np.array([0, 1, 2, 3]))
        self.assertEqual(thresholds[2], .9)
        self.assertEqual(report["pen_false_accept_rate"], 0)
        self.assertEqual(report["balanced_accuracy"], 1)

    def test_calibration_balances_pen_recall_with_false_matches(self):
        from train_roi import calibrate
        scores = np.array([[.99, .01, .01], [.01, .99, .01],
                           [.01, .01, .99], [.01, .01, .89], [.01, .01, .89],
                           [.01, .01, .9]]+[[.01, .01, .1]]*10)
        labels = np.array([0, 1, 2, 2, 2]+[3]*11)
        _, _, _, _, report = calibrate(scores, labels)
        self.assertEqual(report["per_class_recall"][2], 1)
        self.assertLess(report["pen_false_accept_rate"], .1)

    def test_catalog_pen_isolation_removes_disconnected_props(self):
        from train_roi import isolate_catalog_pen
        image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        image.paste((0, 0, 0, 255), (40, 5, 55, 95))
        image.paste((0, 0, 0, 255), (5, 5, 20, 20))
        result = isolate_catalog_pen(image)
        self.assertEqual(result.getbbox(), (40, 5, 55, 95))

    def test_people_negatives_push_all_product_scores_down(self):
        logits = torch.tensor([[2.,2.,2.]],requires_grad=True)
        loss = rejection_loss(logits,torch.tensor([3]))
        loss.backward()
        self.assertTrue(torch.all(logits.grad > 0))

    def test_positive_loss_increases_only_target_score(self):
        logits = torch.zeros((1,3),requires_grad=True)
        rejection_loss(logits,torch.tensor([2])).backward()
        self.assertGreater(logits.grad[0,0],0)
        self.assertGreater(logits.grad[0,1],0)
        self.assertLess(logits.grad[0,2],0)

    def test_output_needs_stability_and_rearms_after_absence(self):
        stable = StableOutput(3)
        labels = ["chips",None,"chips","chips","chips","chips",None,None,None,"chips","chips","chips"]
        events = [stable.update(label) for label in labels]
        self.assertEqual([i for i,value in enumerate(events) if value is not None],[4,11])


if __name__ == "__main__":
    unittest.main()
