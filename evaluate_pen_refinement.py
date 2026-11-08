"""Compare old/refined checkpoints on identical held-out photos and variants."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import torch

from roi_model import ROIClassifier, TARGETS
from train_roi import metrics


def evaluate(path, rows):
    classifier = ROIClassifier(path)
    scores, labels, predictions = [], [], []
    variant_scores, variant_labels = [], []
    for image_path, label in rows:
        frame = cv2.imread(image_path)
        if frame is None:
            raise ValueError(f"Cannot read {image_path}")
        name, values, bounds = classifier.predict_localized(frame)
        if name is not None:
            assert bounds is not None, (image_path, name)
        scores.append(values)
        labels.append(label)
        predictions.append(dict(path=image_path, expected=TARGETS[label] if label < len(TARGETS) else None,
                                predicted=name, scores=values.tolist(),
                                bounds=[int(value) for value in bounds] if bounds else None))
        # These are stress variants of the same photos, not independent samples.
        for gain in (.6, 1.3):
            variant = np.clip(frame.astype(np.float32)*gain, 0, 255).astype(np.uint8)
            _, values = classifier.predict(variant)
            variant_scores.append(values)
            variant_labels.append(label)
    return dict(clean=metrics(np.array(scores), np.array(labels), classifier.threshold, classifier.margin),
                brightness_variants=metrics(np.array(variant_scores), np.array(variant_labels),
                                            classifier.threshold, classifier.margin),
                predictions=predictions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="runs/fullscreen_targets/best.pt")
    parser.add_argument("--refined", default="runs/pens_refined/best.pt")
    parser.add_argument("--split", type=Path, default=Path("data/pen_refinement/split.json"))
    parser.add_argument("--output", type=Path, default=Path("runs/pens_refined/comparison.json"))
    args = parser.parse_args()
    torch.set_num_threads(4)
    splits = json.loads(args.split.read_text())["splits"]
    report = {}
    for model, path in (("baseline", args.baseline), ("refined", args.refined)):
        report[model] = {split: evaluate(path, splits[split]) for split in ("val", "test")}
        print(model, json.dumps({split: report[model][split]["clean"] for split in ("val", "test")}), flush=True)
    args.output.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
