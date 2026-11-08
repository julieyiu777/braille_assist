This is the previous three-class experiment. The active model and runtime are
documented in [ROI_TRAINING.md](ROI_TRAINING.md).

The experiment's classes, in checkpoint order, are `person`, `chips`, and `red_bull`.

Data: 18 annotated person crops, 4 sweet chili chips images, 6 original Red Bull
can images. Person crops come from distinct COCO128 source images (at most one
crop per image). They were inspected in `data/web_products/person_review.jpg`.
Source images and crop coordinates are recorded in
`data/web_products/person_sources.json`. Dataset source:
https://docs.ultralytics.com/datasets/detect/coco128/

Reproduce:

```bash
.venv/bin/python prepare_people.py
.venv/bin/python train_products.py --epochs 15
.venv/bin/python -m unittest test_detection
.venv/bin/python detect.py
```

The per-class seeded split contains 18 training, 5 validation, and 5 test images.
The CNN was selected by validation accuracy, then validation loss. Its best
checkpoint was epoch 4. CNN test: 5/5 correct (3 person, 1 chips, 1 Red Bull).
Linear classifier test: 4/5 correct; one person was classified as chips. These
tiny held-out results are demo checks and do not establish webcam accuracy.
COCO is also used in the pretrained YOLO detector, so this is not an independent
evaluation of YOLO detection accuracy.

Artifacts are saved under `runs/products_three/`: `cnn.pt`, `linear.pt`,
`best.pt`, `split.json`, and `metrics.json`. Older two-class checkpoints remain
in `runs/products/`.

`detect.py` defaults to YOLO26n and `runs/products_three/best.pt`. It selects
the bounding box nearest the screen center. If YOLO recognizes that box as a
person, the callback preserves the person label using the checkpoint's class
index. Otherwise the trained three-class CNN classifies the crop. This avoids
overwriting a known person label with a product prediction, which the former
two-class callback did. The classifier also learns person examples for crops
that the detector assigns to other categories. Only selected-label changes
are printed in the terminal. Press q in the video window to stop.
