"""Pen object proposals, verified by the product classifier before display."""
from pathlib import Path

from roi_model import TARGETS


class PenLocalizer:
    def __init__(self,path,confidence=.1,image_size=384):
        self.path=Path(path)
        if not self.path.is_file():
            raise ValueError('Trained pen-localizer checkpoint is missing')
        self.confidence=confidence
        self.image_size=image_size
        self.model=None

    def proposals(self,frame):
        if self.model is None:
            from ultralytics import YOLO
            self.model=YOLO(str(self.path))
        result=self.model.predict(frame,conf=self.confidence,imgsz=self.image_size,iou=.45,
                                  max_det=3,device='cpu',verbose=False)[0]
        return [(tuple(int(value) for value in box),float(score))
                for box,score in zip(result.boxes.xyxy.cpu().numpy(),result.boxes.conf.cpu().numpy())]


def verify_proposals(classifier,frame,proposals,minimum_detector=.1,minimum_class=.95):
    height,width=frame.shape[:2]
    verified=[]
    for bounds,confidence in proposals:
        if confidence<minimum_detector:
            continue
        x1,y1,x2,y2=bounds
        x1,y1,x2,y2=max(0,x1),max(0,y1),min(width,x2),min(height,y2)
        if x2<=x1 or y2<=y1:
            continue
        padding=max(4,int(.12*max(x2-x1,y2-y1)))
        left,top,right,bottom=max(0,x1-padding),max(0,y1-padding),min(width,x2+padding),min(height,y2+padding)
        label,scores=classifier.predict(frame[top:bottom,left:right])
        if label=='pens' and scores[TARGETS.index('pens')]>=minimum_class:
            verified.append((confidence*float(scores[TARGETS.index('pens')]),scores,(x1,y1,x2,y2)))
    if not verified:
        return None
    _,scores,bounds=max(verified,key=lambda row:row[0])
    return 'pens',scores,bounds
