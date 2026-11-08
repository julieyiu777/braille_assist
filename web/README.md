# Braille Vision web app

Browser front end and detector service for braille_assist: the camera spots an object, the model in this repository identifies it, and the label is spelled out on the 6-servo braille cell.

The detector runs this repository's own pipeline unchanged (`ROIClassifier`, `detect_product`, `DetectionConfirmation`). Everything here only wraps it.

## Layout

| Path | What it is |
| --- | --- |
| `app/BrailleVision.jsx` | Mana app UI (React). Tabs: Camera, Text to braille (UEB grade 1), Insights. Talks to the ESP32 over Web Serial. |
| `server/server.py` | FastAPI detector service: `GET /healthz`, `POST /detect` (raw JPEG body), `POST /release`, `POST /event`, `POST /reload`. |
| `server/model/adapter.py` | Thin adapter that loads this repo (`BRAILLE_ASSIST_REPO`, default `/workspace/braille_assist`) and keeps per-visitor confirmation timers (`X-Session-Id`). |
| `tiger/tiger_sync.py` | Runs every minute: ships detector events into Tiger Data (`braille_events`) and computes the health signals shown in the Insights tab. Needs `TIGER_DATABASE_URL`. |
| `firmware/braille_servo/` | Alternative Arduino (Uno/Nano) 6-servo sketch with a byte-per-cell protocol. |
| `firmware/braille_serial_usb_diagnostic/` | XIAO ESP32C3 diagnostic variant of `firmware/braille_serial` (USB wait, I2C retries, status output). Not yet hardware-tested. |

## Run the detector

```bash
cd web/server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
BRAILLE_ASSIST_REPO=$(git rev-parse --show-toplevel) uvicorn server:app --host 127.0.0.1 --port 8000
```

Hardware protocol for the ESP32 firmware (`firmware/braille_serial`): one lowercase letter plus newline; the board replies `Showing <letter> ...` then `Back at rest.`. The app waits for each acknowledgment before sending the next letter, and only then releases the detector for a new cycle.
