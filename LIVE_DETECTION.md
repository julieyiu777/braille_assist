`detect.py` classifies the **full camera screen** by default. A candidate
displays an orange product box with a large attached label and confidence
percentage. The same accepted label must persist for **more than 3 seconds**
to confirm it. A changed label, missing box, or unclear match resets this timer.
Confirmation turns the box green and prints `chips`, `soda`, or `pens` once.

The confirmed label, confidence, and box are then locked for **10 seconds**.
Classification pauses during the lock while the camera view stays live. After
the lock expires, a new three-second confirmation is required. The top-right
timer shows the confirmation countdown or remaining lock time. Press q to stop.

Boxes are approximate regions estimated from the CNN's class activation map,
not a separately trained object detector. Show one target clearly and large
enough to see its packaging. During the lock the box stays at its confirmed
coordinates. Localization requires a CNN checkpoint
(the default `best.pt` is supported).

```bash
.venv/bin/python detect.py
```

To reduce repeated terminal lines, print only label changes:

```bash
.venv/bin/python detect.py --print-mode changes --stable-frames 5
```

The former placement box is still optional:

```bash
.venv/bin/python detect.py --roi 0 0.5 0.45 1
```

The default checkpoint is `runs/black_pen_active/best.pt`. It expands the pen
class to 128 photos and supports trained pen-region proposals with classifier
verification. See [BLACK_PEN_TRAINING.md](BLACK_PEN_TRAINING.md) for data sources,
results, and remaining camera limitations. For rollback, use
`--classifier runs/rollerball_active/best.pt`. Earlier checkpoints remain in
their original directories.

The targets are chips, soda, and pens. The soda training examples are the former
Red Bull photos, so this relabeling does not establish recognition of soda brands
other than Red Bull. The pen class includes black ballpoint and gel-ink pens,
fountain pens, and a writing example. Wikimedia Commons source pages, attribution,
and licenses are recorded in `data/roi_targets/sources.json`. These web-image
splits are a demo check, not an estimate of live-camera accuracy.

```bash
.venv/bin/python prepare_roi_data.py
.venv/bin/python train_roi.py --epochs 25
.venv/bin/python -m unittest test_detection
.venv/bin/python detect.py
```

The active model, split, and comparison are saved under `runs/black_pen_active/`;
earlier runs are retained in their original directories. The CNN
and pixel-linear baseline learn one score per target; person and scene negatives
push every target score toward zero. Epoch, acceptance threshold, margin, and
model selection use validation data. Data augmentation and background
compositing use training images only. Real camera photos with held-out capture
sessions remain the most useful next evaluation data.

The active output is the terminal. No Braille hardware driver is configured.

Camera collection now saves unannotated frames and timestamped metadata to a
durable local outbox by default, including frames read during the detection
lock. If `TIGER_DATABASE_URL` is configured and the schema is initialized,
metadata uploads to Tiger Data in the background. See [TIGER_DATA.md](TIGER_DATA.md)
for setup, data review/replay, feedback APIs, and the status of the ten requested
features. `--metadata-only` omits image files; `--no-telemetry` disables capture
logging. The window also shows a calm/busy scene-activity indicator.
