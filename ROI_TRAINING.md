The active full-camera version is documented in [LIVE_DETECTION.md](LIVE_DETECTION.md).
This document records the previous bottom-left placement experiment.

The previous detector classified a placement rectangle at the bottom left of the
camera image. Default bounds are x=0–45% and y=50–100%, measured from the top left
of the displayed, unmirrored frame. Align the proposed physical ledge with this
box; adjust it with `--roi X1 Y1 X2 Y2` using coordinates between zero and one.
Only pixels inside that box are used. Hold one item so it fills most of the box.
The rest of the camera view is shown for alignment but cannot influence output.

```bash
.venv/bin/python detect.py
.venv/bin/python detect.py --roi 0.05 0.55 0.4 0.95
```

The runtime no longer depends on YOLO finding a bounding box or ranking object
confidence. It directly classifies the placement crop. There are only two output
scores: chips and red_bull. People and non-target scenes train both scores toward
zero through binary cross entropy, with negative loss weighted by 2. People are
not an output class. Low scores or ambiguous scores are rejected. The selected
CNN's validation-selected acceptance threshold is 0.95 and score margin is 0.35;
these sigmoid scores are not calibrated probabilities of correctness.

Five consecutive accepted frames are required before printing a target label.
No clear match produces no terminal label. Five rejected frames rearm output
for the next item. The window shows the placement box, scores, and a no-match
message for alignment. Press q to stop. Only `chips` or `red_bull` is printed to
stdout. No Braille device driver or hardware protocol exists in this repository;
stdout is the implemented handoff, not a live Braille-pad connection.

Dataset: 7 chips images (Hal's sweet chili and Lay's bags), 10 original Red Bull
images (including four hand-held photos), and 42 negatives (18 annotated person
crops and 24 COCO scenes). It does not establish recognition of all chips brands
or every Red Bull edition. Source manifests are in `data/roi_targets/sources.json`,
`data/web_products/sources.json`, and `data/web_products/person_sources.json`.
Additional Red Bull photos came from Unsplash and Place Media. Product-photo
near duplicates were screened by a perceptual hash and visual contact sheet;
similar catalog artwork can remain. Original COCO images used for person crops
are excluded from the scene negatives to prevent source overlap across splits.

The seeded split is 41 train / 9 validation / 9 test. Each held-out set contains
one chips image, two Red Bull images, and six negatives. Augmentation happens
only after splitting. It preserves aspect ratio, adds color/rotation/scale
variation, removes border-connected catalog white backgrounds, and places
products on training-only negative backgrounds. Empty solid-color areas are also
used during negative training. Validation and test images are not augmented.

Both CNN and pixel-linear baselines are trained. Epoch, acceptance threshold,
margin, and model selection use validation data only. CNN test: 9/9 correct,
including rejection of 6/6 negatives. Linear test: 7/9 correct, confusing the two
Red Bull examples with chips. This tiny web-image test is a demo check, not evidence of reliable
webcam performance. Real photos from the intended ledge, with held-out capture
sessions, are the next useful evaluation data. Rejection cannot guarantee that
every unseen non-target will be ignored.

```bash
.venv/bin/python prepare_roi_data.py
.venv/bin/python train_roi.py
.venv/bin/python -m unittest test_detection
.venv/bin/python detect.py
```

Data preparation expects the earlier `data/web_products` images/person crops and
`data/coco128.zip` to exist. The trained runtime checkpoint is
`runs/roi_targets/best.pt`. Split and metrics are saved beside it. Previous
two- and three-class experiments are retained in their original directories.
