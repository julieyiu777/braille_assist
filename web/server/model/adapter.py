"""Braille Vision adapter for Julie's braille_assist repository (unmodified).

Uses her own pipeline exactly as `detect.py` runs it:
  roi_model.ROIClassifier(runs/black_pen_active/best.pt)  -> chips / soda / pens
  roi_detect.detect_product(...)                          -> label, scores, box (full frame ROI)
  roi_detect.DetectionConfirmation                        -> >3 s confirm (her class, unmodified)
After a confirmation the adapter holds the lock until the app calls release() (POST /release), i.e. once every
letter has been shown on the servo cell, instead of her class's fixed 100 s. MAX_HOLD_SECONDS is only a failsafe.
The repo lives at /workspace/braille_assist (git clone of github.com/julieyiu777/braille_assist).
"""
import os, sys, time, threading
from pathlib import Path
import numpy as np

REPO = Path(os.environ.get("BRAILLE_ASSIST_REPO", "/workspace/braille_assist"))
CHECKPOINT = REPO / "runs/black_pen_active/best.pt"
sys.path.insert(0, str(REPO))

import torch
from roi_model import ROIClassifier, TARGETS
from roi_detect import detect_product, DetectionConfirmation

torch.set_num_threads(4)
_cwd = os.getcwd()
os.chdir(REPO)  # her checkpoint references the pen localizer by a repo-relative path
try:
    _classifier = ROIClassifier(str(CHECKPOINT))
finally:
    os.chdir(_cwd)
if _classifier.pen_localizer is not None and not _classifier.pen_localizer.path.is_absolute():
    # Checkpoint stores a repo-relative path; resolve it against the repo root.
    _classifier.pen_localizer.path = REPO / _classifier.pen_localizer.path

MODEL_NAME = "braille_assist · black_pen_active"


def _measure_policy():
    """Read the confirm and lock durations from her DetectionConfirmation instead of hardcoding them."""
    probe, box, scores = DetectionConfirmation(1), (0, 0, 1, 1), np.zeros(len(TARGETS))
    probe.observe(TARGETS[0], scores, box, 0.0)
    t = 0.0
    while t < 60 and not probe.observe(TARGETS[0], scores, box, t):
        t = round(t + 0.05, 2)
    return round(t, 1), round(probe.until - t, 1)


CONFIRM_SECONDS, LOCK_SECONDS = _measure_policy()  # LOCK_SECONDS = her class's fixed lock, reported for reference
MAX_HOLD_SECONDS = 300  # failsafe if a browser never reports that playback finished
_lock = threading.Lock()
_sessions = {}  # one DetectionConfirmation per visitor, so timers never mix between people


def _confirmation_for(session):
    now = time.monotonic()
    for k in [k for k, (_, seen) in _sessions.items() if now - seen > 600]:
        del _sessions[k]
    conf = _sessions.get(session, (DetectionConfirmation(1), now))[0]
    _sessions[session] = (conf, now)
    return conf


def _git_rev():
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def info():
    return {"repo": "github.com/julieyiu777/braille_assist", "checkpoint": str(CHECKPOINT.relative_to(REPO)),
            "classes": TARGETS, "confirmSeconds": CONFIRM_SECONDS, "lockSeconds": LOCK_SECONDS, "commit": _git_rev(),
            "lockMode": "until-playback", "maxHoldSeconds": MAX_HOLD_SECONDS,
            "policy": f"label must persist >{CONFIRM_SECONDS:g} s to confirm, then holds until every letter has been shown on the servos"}


def release(session="default"):
    """End the hold for this visitor so the next frame starts a new detection cycle."""
    with _lock:
        entry = _sessions.get(session or "default")
        if not entry:
            return {"released": False}
        conf = entry[0]
        was = conf.detection is not None
        conf.until = 0
        conf.locked(time.monotonic())  # her class clears detection + candidate once the lock has expired
        return {"released": was}


def detect(img, session="default"):
    bgr = np.asarray(img)[:, :, ::-1].copy()
    with _lock:
        _confirmation = _confirmation_for(session or "default")
        now = time.monotonic()
        confirmed = False
        if _confirmation.locked(now):
            label, scores, bounds = _confirmation.detection
        else:
            label, scores, bounds = detect_product(_classifier, bgr, (0, 0, 1, 1))
            now = time.monotonic()
            confirmed = _confirmation.observe(label, scores, bounds, now)
            if confirmed:
                _confirmation.until = now + MAX_HOLD_SECONDS  # held until release(), not her fixed 100 s
        locked = _confirmation.locked(now)
        if locked:
            phase, remaining = "locked", None  # waiting for the servos to finish spelling the word
        elif _confirmation.candidate is not None:
            phase, remaining = "confirming", max(0.0, CONFIRM_SECONDS - (now - _confirmation.since))
        else:
            phase, remaining = "scanning", None
    dets = []
    if label is not None and bounds is not None:
        dets.append({"label": label, "confidence": float(scores[TARGETS.index(label)]),
                     "box": [float(v) for v in bounds]})
    return {
        "detections": dets,
        "scores": {t: float(s) for t, s in zip(TARGETS, scores)},
        "state": {"phase": phase, "remaining": None if remaining is None else round(remaining, 2),
                  "confirmed": confirmed, "label": label if (locked or phase == "confirming") else None},
    }
