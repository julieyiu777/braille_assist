The active full-camera model is documented in [LIVE_DETECTION.md](LIVE_DETECTION.md).
It outputs only chips or Red Bull and trains people/backgrounds as negatives.

The previous default was **person, chips, red_bull**. See
[TRAINING_THREE_CLASSES.md](TRAINING_THREE_CLASSES.md) for current commands and
results. The two-class setup below is retained as the earlier experiment;
explicitly pass `--classes chips red_bull --data data/products --output runs/products`
to reproduce it.

Put independent product photos into these folders:

```
data/products/chips/
data/products/red_bull/
```

The reference download uses the existing URLs in `detect.py`, except chips:
that existing URL depicts barbecue chips and is excluded from training. Add
sweet chili photos manually. The downloaded barbecue image was moved outside
the dataset to `data/excluded/barbecue_reference.jpg`. At least three unique
photos per class are required; aim for 30 or more, covering different angles,
backgrounds, distances, and lighting. Use cropped product photos to match the
detector's webcam crops. Sequential video frames and edited copies of a reference
are not independent samples. Do not spread those across splits.

Run both classifiers with a seeded, per-class split targeting 70% training,
15% validation, and 15% test (rounded to keep each split populated):

```bash
.venv/bin/python train_products.py --download-references
```

Chips and Red Bull are the default classes. To include the charger later:

```bash
.venv/bin/python train_products.py --classes chips red_bull charger
```

The CNN fine-tunes the last block and classification layer of ImageNet-pretrained
ResNet18. The separate linear baseline uses normalized 32x32 RGB pixels with a
single linear layer and cross-entropy loss. Neither trains YOLO bounding boxes.
Training augmentations are applied only after splitting original images. Exact
decoded duplicates are removed; near-duplicates and capture sessions still need
manual review. The split is saved in `runs/products/split.json`.

Each model selects its checkpoint using validation loss, then evaluates once on
the test split. `runs/products/metrics.json` contains accuracy, cross-entropy loss,
per-class recall, and confusion matrices (rows: actual, columns: predicted, in
the class order from `split.json`). Model weights are saved as `cnn.pt` and
`linear.pt`. Tiny test sets cannot establish real webcam accuracy.

The classifier with the highest validation accuracy (then lowest validation loss
to break ties) is copied to `best.pt`. Test metrics do not select the model.
`detect.py` loads `runs/products/best.pt` by default, with YOLO26n finding the
center object and the trained classifier identifying its crop. To select a
specific classifier, use `--classifier runs/products/linear.pt` or `cnn.pt`.

The web-image demo can be reproduced with:

```bash
.venv/bin/python collect_products.py
.venv/bin/python train_products.py --data data/web_products --epochs 15
.venv/bin/python detect.py
```

Image URLs and exclusions are recorded in `data/web_products/sources.json`.
Search candidates were reviewed as sweet chili chips or original Red Bull cans;
Red Bull images include several can sizes. Catalog images may still share source
artwork. Treat the tiny held-out catalog-image results as demo checks, not an
estimate of performance on real webcam images. The classifier has only two
classes: unrelated crops will also receive one of those labels. YOLO26n's
existing detector was not fine-tuned, so an object it misses cannot be classified.
