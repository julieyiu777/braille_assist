# Braille Assist

Camera-based recognition of chips, soda, and pens, with timestamped data
collection and an optional Tiger Data connection. Detection boxes show the
predicted label and confidence. A label must persist for more than three
seconds before a 100-second confirmation lock.

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
under `runs/telemetry/`; use `--metadata-only` to omit images.
Tiger Data is disabled by default, even if `.env` contains a connection URL.
Use `--tiger-data` to explicitly enable cloud uploads; that mode requires a
working database and stops detection if an upload fails.

```bash
python -m unittest test_detection test_black_pen_data test_tiger_data
```

## Documentation

- [Live detection](LIVE_DETECTION.md)
- [Black-pen dataset, training, evaluation, and limitations](BLACK_PEN_TRAINING.md)
- [Tiger Data setup and feature status](TIGER_DATA.md)
- [ESP32 six-servo Braille output](BRAILLE_OUTPUT.md)
- [Earlier rollerball experiment](ROLLERBALL_TRAINING.md)

For Tiger Data, copy `.env.example` to `.env`, supply your connection URI, and
follow the setup guide. Credentials and private camera data are excluded from
Git. Raw annotation-download caches and intermediate training checkpoints are
also excluded; collection and training scripts can regenerate those artifacts.
Historical experiments may require retraining their referenced checkpoints.

Training-image provenance and individual license information are retained in
the dataset manifests. Public availability does not imply a single blanket
license for all images or model dependencies.

For an ESP32 running the supplied firmware, use
`python detect.py --braille-port auto` to display confirmed labels one character
at a time. See the hardware guide for uploading the firmware and selecting a
USB port. Without that option, detection runs without sending hardware commands.

This is an experimental product-recognition project. Webcam pen recognition
remains imperfect. Physical servo position feedback is not configured.
