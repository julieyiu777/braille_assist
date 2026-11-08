# Braille Assist

Camera-based recognition of chips, soda, and pens, with timestamped data
collection and an optional Tiger Data connection. Detection boxes show the
predicted label and confidence. A label must persist for more than three
seconds before a ten-second confirmation lock.

## Run

Use Python 3.12 and run commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python detect.py
```

The active classifier and pen-localizer checkpoints are included. Press `q`
to quit. Camera collection saves raw, unannotated JPEGs and metadata locally
under `runs/telemetry/`; use `--no-telemetry` to disable collection or
`--metadata-only` to omit images.

```bash
python -m unittest test_detection test_black_pen_data test_tiger_data
```

## Documentation

- [Live detection](LIVE_DETECTION.md)
- [Black-pen dataset, training, evaluation, and limitations](BLACK_PEN_TRAINING.md)
- [Tiger Data setup and feature status](TIGER_DATA.md)
- [Earlier rollerball experiment](ROLLERBALL_TRAINING.md)

For Tiger Data, copy `.env.example` to `.env`, supply your connection URI, and
follow the setup guide. Credentials and private camera data are excluded from
Git. Raw annotation-download caches and intermediate training checkpoints are
also excluded; collection and training scripts can regenerate those artifacts.
Historical experiments may require retraining their referenced checkpoints.

Training-image provenance and individual license information are retained in
the dataset manifests. Public availability does not imply a single blanket
license for all images or model dependencies.

This is an experimental product-recognition project. Webcam pen recognition
remains imperfect. No physical Braille pin/servo driver is configured.
