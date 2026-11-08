"""Classify the camera view and print identified target labels to stdout."""
import argparse
import time

import cv2
import numpy as np
import torch

from roi_model import ROIClassifier, TARGETS
from tiger_data import CaptureLog, EventStore, TigerSync, utc_now


def roi_bounds(shape, normalized):
    height,width = shape[:2]
    x1,y1,x2,y2 = normalized
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ValueError("ROI must satisfy 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1")
    bounds = (int(x1*width),int(y1*height),int(x2*width),int(y2*height))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError("ROI is smaller than one pixel")
    return bounds


def predict_roi(classifier, frame, normalized):
    x1,y1,x2,y2 = roi_bounds(frame.shape,normalized)
    label,scores = classifier.predict(frame[y1:y2,x1:x2])
    return label,scores,(x1,y1,x2,y2)


def detect_product(classifier, frame, normalized):
    x1, y1, x2, y2 = roi_bounds(frame.shape, normalized)
    label, scores, bounds = classifier.predict_localized(frame[y1:y2, x1:x2])
    if bounds is not None:
        left, top, right, bottom = bounds
        bounds = (left+x1, top+y1, right+x1, bottom+y1)
    return label, scores, bounds


class DetectionConfirmation:
    """Confirm a continuous label after three seconds, then hold for ten."""
    def __init__(self, stable_frames=1):
        self.stable_frames = stable_frames
        self.candidate = None
        self.since = None
        self.count = 0
        self.detection = None
        self.until = 0

    def locked(self, now):
        if self.detection is not None and now >= self.until:
            self.detection = None
            self.candidate, self.since, self.count = None, None, 0
        return self.detection is not None

    def observe(self, label, scores, bounds, now):
        if self.locked(now):
            return False
        if label is None or bounds is None:
            self.candidate, self.since, self.count = None, None, 0
            return False
        if label != self.candidate:
            self.candidate, self.since, self.count = label, now, 1
        else:
            self.count += 1
        if now-self.since > 3 and self.count >= self.stable_frames:
            self.detection = (label, scores.copy(), bounds)
            self.until = now+10
            return True
        return False

    def timer_text(self, now):
        if self.locked(now):
            return f"Locked: {max(0, self.until-now):.1f}s"
        if self.candidate is not None:
            return f"Confirming: {max(0, 3-(now-self.since)):.1f}s"
        return "Scanning..."


def draw_tag(frame, text, x, y, color, scale=1.0):
    """Draw a readable filled label, keeping it within the camera image."""
    height, width = frame.shape[:2]
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    if tw+20 > width:
        scale *= max(1, width-20)/tw
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    box_w, box_h = tw+20, th+baseline+16
    x = max(0, min(x, width-box_w))
    y = max(0, min(y, height-box_h))
    cv2.rectangle(frame, (x, y), (x+box_w, y+box_h), color, -1)
    cv2.putText(frame, text, (x+10, y+8+th), cv2.FONT_HERSHEY_SIMPLEX,
                scale, (0, 0, 0), 2, cv2.LINE_AA)


class StableOutput:
    """Emit on stable appearance/change; rearm after stable absence."""
    def __init__(self, frames=5):
        self.frames = frames
        self.candidate = None
        self.count = 0
        self.last_emitted = None

    def update(self,label):
        if label == self.candidate:
            self.count += 1
        else:
            self.candidate,self.count = label,1
        if self.count < self.frames:
            return None
        if label is None:
            self.last_emitted = None
            return None
        if label != self.last_emitted:
            self.last_emitted = label
            return label
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",default="0")
    parser.add_argument("--classifier",default="runs/black_pen_active/best.pt")
    parser.add_argument("--roi",nargs=4,type=float,default=(0,0,1,1),
                        metavar=("X1","Y1","X2","Y2"),
                        help="Normalized classification rectangle (default: full camera image).")
    parser.add_argument("--threshold",type=float,default=None,help="Override validation-selected acceptance score.")
    parser.add_argument("--margin",type=float,default=None,help="Override minimum separation between target scores.")
    parser.add_argument("--stable-frames",type=int,default=1)
    parser.add_argument("--print-mode",choices=("frame","changes"),default="frame",
                        help="Print each confirmed detection (default), or only confirmed label changes.")
    parser.add_argument("--save",action="store_true",help="Save the annotated webcam view to a new video file.")
    parser.add_argument("--telemetry-db", default="runs/telemetry/events.sqlite3")
    parser.add_argument("--device-id", default="camera-1")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--location", nargs=2, type=float, metavar=("LATITUDE", "LONGITUDE"))
    parser.add_argument("--no-telemetry", action="store_true", help="Disable camera data collection.")
    parser.add_argument("--metadata-only", action="store_true", help="Log metadata without raw frames (disables image replay).")
    args = parser.parse_args()
    try:
        roi_bounds((100,100,3),args.roi)
    except ValueError as error:
        parser.error(str(error))
    if args.stable_frames < 1 or any(value is not None and not 0 <= value <= 1 for value in (args.threshold,args.margin)):
        parser.error("stable-frames must be positive; threshold/margin must be between 0 and 1")
    if args.location and not (-90 <= args.location[0] <= 90 and -180 <= args.location[1] <= 180):
        parser.error("location must be a valid latitude and longitude")
    torch.set_num_threads(4)
    classifier = ROIClassifier(args.classifier,args.threshold,args.margin)
    source = int(args.source) if args.source.isdecimal() else args.source
    camera = cv2.VideoCapture(source)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError(f"Cannot open camera/video: {source}")
    stable = StableOutput(1)
    confirmation = DetectionConfirmation(args.stable_frames)
    writer = None
    store, capture_log, sync = None, None, None
    try:
        if not args.no_telemetry:
            import sys
            store = EventStore(args.telemetry_db, args.device_id)
            capture_log = CaptureLog(store, args.classifier, args.roi, args.user_id, not args.metadata_only,
                source=args.source, threshold=getattr(classifier,'threshold',None),
                margin=getattr(classifier,'margin',None), targets=TARGETS)
            sync = TigerSync(store)
            print(f'Capture log: {store.path}; session {store.session_id}', file=sys.stderr)
            if args.location:
                for hazard in store.nearby_hazards(*args.location):
                    store.record('hazard_warning', label=hazard['label'], severity=hazard['severity'],
                        description=f"Previously reported {hazard['label']} within {hazard['distance_m']} m",
                        location=args.location, payload=hazard)
        while True:
            ok,frame = camera.read()
            if not ok:
                break
            captured_at = utc_now()
            now = time.monotonic()
            confirmed = False
            fresh = not confirmation.locked(now)
            if not fresh:
                label, scores, bounds = confirmation.detection
            else:
                label, scores, bounds = detect_product(classifier, frame, args.roi)
                now = time.monotonic()
                confirmed = confirmation.observe(label, scores, bounds, now)
                if label is None or bounds is None:
                    stable.update(None)
                if confirmed:
                    event = stable.update(label)
                    output = label if args.print_mode == "frame" else event
                    if output is not None:
                        print(output, flush=True)
            is_locked = confirmation.locked(now)
            busy = None
            if capture_log:
                busy = capture_log.record_frame(frame, label, scores, bounds, fresh=fresh,
                    confirmed=confirmed, locked=is_locked, captured_at=captured_at, location=args.location)
            if label is not None and bounds is not None:
                x1, y1, x2, y2 = bounds
                color = (0, 220, 0) if is_locked else (0, 180, 255)
                cv2.rectangle(frame, (x1, y1), (x2-1, y2-1), color, 3)
                # Anchor the filled label directly inside the box's top edge.
                confidence = float(scores[TARGETS.index(label)])
                status = "Confirmed" if is_locked else "Confirming"
                tag = f"{label.replace('_', ' ').title()}  {confidence:.1%} confidence ({status})"
                draw_tag(frame, tag, x1, y1, color, 1.2)
            else:
                draw_tag(frame, "Show one target clearly", 10, frame.shape[0]-55,
                         (0, 180, 255), .8)
            timer = confirmation.timer_text(now)
            (timer_width, _), _ = cv2.getTextSize(timer, cv2.FONT_HERSHEY_SIMPLEX, .8, 2)
            draw_tag(frame, timer, frame.shape[1]-timer_width-30, 10, (0, 220, 255), .8)
            if busy is not None:
                activity = f"Scene activity: {'busy' if busy >= .65 else 'calm'}"
                (activity_width, _), _ = cv2.getTextSize(activity, cv2.FONT_HERSHEY_SIMPLEX, .65, 2)
                draw_tag(frame, activity, frame.shape[1]-activity_width-30, 55, (0, 220, 255), .65)
            cv2.imshow("Product recognition",frame)
            if args.save:
                if writer is None:
                    from pathlib import Path
                    Path("runs/fullscreen_targets").mkdir(parents=True,exist_ok=True)
                    path = f"runs/fullscreen_targets/webcam_{time.time_ns()}.mp4"
                    fps = camera.get(cv2.CAP_PROP_FPS)
                    fps = fps if np.isfinite(fps) and fps > 0 else 30
                    writer = cv2.VideoWriter(path,cv2.VideoWriter_fourcc(*"mp4v"),fps,(frame.shape[1],frame.shape[0]))
                    if not writer.isOpened():
                        raise RuntimeError(f"Cannot save video to {path}")
                writer.write(frame)
            if cv2.waitKey(1) & 0xff == ord("q") or cv2.getWindowProperty("Product recognition",cv2.WND_PROP_VISIBLE) < 1:
                break
    except KeyboardInterrupt:
        pass
    finally:
        camera.release()
        if sync:
            sync.close()
        if store:
            store.close()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
