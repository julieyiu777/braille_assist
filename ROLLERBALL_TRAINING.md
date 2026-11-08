This training run targets the thick, glossy black pen with gold trim in the
user's Magnific reference. The reference itself is excluded from training and
threshold calibration; it is a separate requested-example check. Both the
previous checkpoint and the new one are evaluated on it.

The live detector now uses `runs/rollerball_active/best.pt`. Candidate selection
retained epoch 1 from the 30-epoch run initialized from the previous refined
checkpoint. A second run used the original checkpoint, 15 epochs, and a lower
learning rate of 0.00002. Both candidates had equal clean validation metrics;
the first candidate wins the documented tie. Selection details are stored in
`runs/rollerball_active/selection.json`.

On the common validation set, pen recognition increased from 4/7 to 5/7 with
zero false pen matches among 13 non-pen images for both models. Overall
validation correctness increased from 17/20 to 18/20. Overall test results were
unchanged: 16/19 correct, 4/6 pens recognized, and 1/13 non-pen images falsely
accepted as pens. Within the newly held-out luxury styles, recognition increased
from 1/3 to 2/3 images, while one earlier generic pen was lost. These results
show a style-specific tradeoff, not a demonstrated overall test accuracy gain.
Small/context-heavy pen scenes and an in-hand Cross Edge example remain misses.

The chosen thresholds are 0.95 for chips/soda and 0.85 for pens, with a 0.35
score margin. Previous checkpoints remain available. Camera-session photos of
the actual pens and false matches are needed to measure live-camera accuracy.

Web search identified product photography from Dayspring Pens, Sheaffer,
Dromgoole's, Penworld, and Pianki. Eighteen reviewed photos cover eight pen
models, including black/gold rollerballs, capped and uncapped views, desk
scenes, and pens held in a hand. Source-page and image URLs are recorded in
`data/rollerball_pens/reviewed_sources.json`. Product photography retains its
source ownership; these files are not presented as open-license images.

All views of each product model stay in one partition:

- Training: Alexandria, Monroe, Parker Meridian, Sheaffer 300, Sheaffer VFM.
- Validation: Sheaffer 100, including its different finishes and views.
- Test: Cross Edge and Aurora Ipsilon.

The prior reviewed dataset remains in its original partitions. The combined
split has 74 training, 20 validation, and 19 test images. Augmentations of one
photo remain in its original training partition. Catalog photos are not
equivalent to live-camera examples; the small held-out counts cannot establish
webcam accuracy.

Training removes disconnected gift boxes and logos from the pen foreground
when compositing white-background catalog photos. It also varies orientation,
scale, placement, brightness, and backgrounds. Real scenes retain their
backgrounds. Threshold/epoch selection now optimizes balanced accuracy minus
half the pen false-accept rate, balancing true pen recognition with rejection
of look-alikes. This replaces the earlier rule that always preferred zero
false pen matches, even when it rejected most true pens. Test data is used only
for reporting after selection.

Reproduce collection, reviewed split construction, and training:

```bash
.venv/bin/python collect_rollerball_pens.py
.venv/bin/python build_rollerball_split.py
.venv/bin/python train_roi.py --split data/rollerball_pens/split.json --output runs/rollerball_targets --epochs 30 --kinds cnn --init runs/pens_refined/best.pt
.venv/bin/python train_roi.py --split data/rollerball_pens/split.json --output runs/rollerball_slow --epochs 15 --kinds cnn --init runs/fullscreen_targets/best.pt --learning-rate 0.00002
.venv/bin/python select_rollerball_model.py
.venv/bin/python -m unittest test_detection
.venv/bin/python evaluate_pen_refinement.py --baseline runs/pens_refined/best.pt --refined runs/rollerball_active/best.pt --split data/rollerball_pens/split.json --output runs/rollerball_active/comparison.json
```
