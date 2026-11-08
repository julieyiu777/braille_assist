
"""Braille Vision detection server.

Model resolution order (first match wins):
  1. model/adapter.py defining  detect(img: PIL.Image) -> list[dict(label, confidence, box=[x1,y1,x2,y2])]
     -> use this to wrap your own repository's inference code.
  2. Any *.pt / *.onnx weights in model/  -> loaded with Ultralytics YOLO.
  3. Fallback: stock YOLOv8n (COCO, 80 classes) so the pipeline works end to end today.
"""
import glob, importlib.util, io, json, os, sys, time, threading, uuid
from datetime import datetime, timezone
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(ROOT, "model")
PORT = int(open(os.path.join(ROOT, "PORT")).read().strip())

app = FastAPI(title="Braille Vision")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_lock = threading.Lock()
# Event log: one JSON line per event in events/queue.jsonl. The tiger-sync Mana task ships it to Tiger Data every
# minute (this process holds no database credentials). Images are never logged.
EVENT_DIR = os.path.join(ROOT, "events"); os.makedirs(EVENT_DIR, exist_ok=True)
QUEUE = os.path.join(EVENT_DIR, "queue.jsonl")
_qlock = threading.Lock()
def log_event(kind, session=None, **fields):
    rec = {"time": datetime.now(timezone.utc).isoformat(), "id": str(uuid.uuid4()), "kind": kind, "session": session,
           "model": _state.get("name"), **{k: v for k, v in fields.items() if v is not None}}
    try:
        with _qlock, open(QUEUE, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception as e:
        print("event log failed:", e, file=sys.stderr)
_state = {"detect": None, "name": None, "source": None}

def _load():
    adapter = os.path.join(MODEL_DIR, "adapter.py")
    if os.path.exists(adapter):
        sys.path.insert(0, MODEL_DIR)
        spec = importlib.util.spec_from_file_location("bv_adapter", adapter)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        _state.update(detect=mod.detect, release=getattr(mod, "release", None), name=getattr(mod, "MODEL_NAME", "custom adapter"), source="adapter",
                      info=mod.info() if hasattr(mod, "info") else None)
        return
    from ultralytics import YOLO
    weights = sorted(glob.glob(os.path.join(MODEL_DIR, "*.pt")) + glob.glob(os.path.join(MODEL_DIR, "*.onnx")))
    path, source = (weights[0], "weights") if weights else (os.path.join(ROOT, "yolov8n.pt"), "fallback")
    model = YOLO(path)
    def detect(img, conf=0.35):
        r = model.predict(img, conf=conf, verbose=False, imgsz=640)[0]
        out = []
        for b in r.boxes:
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
            out.append({"label": r.names[int(b.cls)], "confidence": float(b.conf), "box": [x1, y1, x2, y2]})
        return out
    _state.update(detect=detect, name=os.path.basename(path), source=source)

_load()

@app.get("/healthz")
def healthz():
    return {"ok": True, "model": _state["name"], "source": _state["source"], **(_state.get("info") or {})}

@app.post("/reload")
def reload():
    with _lock:
        _load()
    return healthz()

@app.post("/release")
def release(request: Request):
    """Called by the app once every letter of the confirmed word has been shown: start a new detection cycle."""
    fn = _state.get("release")
    if not fn:
        return {"released": False}
    session = (request.headers.get("x-session-id") or "default")[:64]
    out = fn(session)
    log_event("release", session, detail={"released": bool(out.get("released"))})
    return out

_PLAYBACK_KEYS = {"word": str, "source": str, "hardware": bool, "cells": int, "durationMs": int, "stopped": bool, "error": str}
@app.post("/event")
async def event(request: Request):
    """Playback reports from the app (word spelled, on servos or screen, how long it took)."""
    body = await request.body()
    if len(body) > 2048:
        raise HTTPException(413, "event too large")
    try:
        data = json.loads(body or b"{}")
    except Exception:
        raise HTTPException(400, "send JSON")
    session = (request.headers.get("x-session-id") or "default")[:64]
    if data.get("kind") == "error":  # board / detector errors seen by the app
        src, msg = data.get("source"), data.get("message")
        if not isinstance(src, str) or not isinstance(msg, str):
            raise HTTPException(400, "error needs source and message")
        log_event("error", session, label=src[:40], detail={"source": src[:40], "message": msg[:300]})
        return {"ok": True}
    if data.get("kind") != "playback":
        raise HTTPException(400, "unsupported kind")
    detail = {}
    for k, t in _PLAYBACK_KEYS.items():
        v = data.get(k)
        if isinstance(v, t) and not (t is int and isinstance(v, bool)):
            detail[k] = v[:200] if isinstance(v, str) else v
    log_event("playback", session, label=detail.get("word"), detail=detail)
    return {"ok": True}

@app.post("/detect")
async def detect(request: Request):
    body = await request.body()
    if not body:
        raise HTTPException(400, "send a JPEG/PNG image as the request body")
    try:
        img = Image.open(io.BytesIO(body)).convert("RGB")
    except Exception:
        raise HTTPException(400, "body is not a decodable image")
    t = time.time()
    session = (request.headers.get("x-session-id") or "default")[:64]
    with _lock:
        try:
            out = _state["detect"](img, session=session)
        except TypeError:
            out = _state["detect"](img)
    extra = {}
    if isinstance(out, dict):  # adapters may return {"detections": [...], ...extra fields}
        extra = {k: v for k, v in out.items() if k != "detections"}
        out = out.get("detections", [])
    dets = sorted(out, key=lambda d: -d["confidence"])
    ms = round((time.time() - t) * 1000)
    st = extra.get("state") or {}
    top = dets[0] if dets else None
    sc = sorted((extra.get("scores") or {}).values(), reverse=True)
    try:
        rtt = int(request.headers.get("x-prev-rtt") or 0) or None  # app's round trip for its previous frame
        rtt = rtt if rtt and rtt < 60000 else None
    except ValueError:
        rtt = None
    fields = dict(label=top["label"] if top else None, confidence=round(top["confidence"], 4) if top else None,
                  phase=st.get("phase"), server_ms=ms, rtt_ms=rtt, margin=round(sc[0] - (sc[1] if len(sc) > 1 else 0), 4) if sc else None,
                  scores={k: round(v, 4) for k, v in (extra.get("scores") or {}).items()} or None)
    log_event("frame", session, **fields)
    if st.get("confirmed"):
        log_event("confirm", session, **fields)
    return {"model": _state["name"], "source": _state["source"], "width": img.width, "height": img.height,
            "ms": ms, "detections": dets, **extra}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
