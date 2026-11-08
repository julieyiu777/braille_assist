Pen refinement targets false positives: objects such as pencils, scissors,
remotes, keys, rulers, USB drives, and spoons being accepted as pens.

This records the earlier `runs/pens_refined/best.pt` checkpoint. The live
detector now uses the subsequent [rollerball training run](ROLLERBALL_TRAINING.md).
On this earlier run's expanded
16-image test set, false pen matches decreased from 2/13 non-pen images to 1/13;
all three pen images were recognized. Overall correct classifications increased
from 12/16 to 15/16. On validation, false pen matches fell from 2/13 to 0/13,
but pen recognition fell from 3/3 to 1/3: small pens within wider desk/writing
scenes remain a limitation. These counts are exploratory web-image checks,
not evidence that live-camera false positives have been eliminated.

The chosen model was trained from ImageNet features for 30 epochs, with epoch
18 retained. A second 18-epoch run fine-tuning the old checkpoint was also
evaluated; it did not improve clean validation recognition. Selection and
subsequent threshold calibration use validation data. The selected thresholds
are chips 0.95, soda 0.95, pens 0.85, with a 0.35 score margin. For pen cutoffs
with equal validation results, calibration chooses the least strict cutoff to
avoid rejecting additional true pens without evidence of fewer false matches.

Per-image outputs and brightness stress results are in
`runs/pens_refined/comparison.json`. Run `.venv/bin/python detect.py` to use the
refined model. To return to the old model, use
`.venv/bin/python detect.py --classifier runs/fullscreen_targets/best.pt`.

The reviewed dataset adds six original pen photos and twelve non-pen photos
from Wikimedia Commons. It excludes two original training images (a collection
of pens and a wide desk scene) that encourage background shortcuts. Original
validation and test images stay in their original partitions. The combined
split contains 63 training, 16 validation, and 16 test originals. All 95 images
have distinct decoded pixel hashes. Augmentation is applied only to training
images; this small web dataset still needs live-camera evaluation.

Source pages, author/license metadata, and visual review decisions are saved in
`data/pen_refinement/reviewed_sources.json`. Search results that are unrelated,
duplicated, illustrations, or too tightly cropped are excluded explicitly.
The original photos remain on disk for reproducibility.

Training changes:

- Preserve real scene backgrounds. Remove white backgrounds only when the
  image border is predominantly white.
- Include wider pen rotations and product scales during training.
- Balance sampling across product and negative classes.
- Calibrate a separate pen acceptance threshold using validation images.
  Validation selection prioritizes reducing false pen matches, then balanced
  recognition accuracy. The test set is used for reporting, not selection.

`--threshold` remains an explicit override for every class. Without it, inference
uses the thresholds stored in the checkpoint. Confidence is a model score,
not a calibrated probability of correctness. Class activation boxes remain
approximate; retraining is aimed at classification errors.

Download and review the sources:

```bash
.venv/bin/python refine_pen_data.py
.venv/bin/python build_pen_split.py
```

Train a candidate from ImageNet features or fine-tune the original checkpoint:

```bash
.venv/bin/python train_roi.py --split data/pen_refinement/split.json --output runs/pens_refined --epochs 30 --kinds cnn
.venv/bin/python train_roi.py --split data/pen_refinement/split.json --output runs/pens_finetuned --epochs 18 --kinds cnn --init runs/fullscreen_targets/best.pt
.venv/bin/python -m unittest test_detection
```

The original model is retained under `runs/fullscreen_targets/`. The comparison
script writes predictions, confusion matrices, and brightness stress checks for
the old and chosen model on identical held-out images. Brightness variants are
derived checks, not additional independent test photos.

```bash
.venv/bin/python evaluate_pen_refinement.py
```
