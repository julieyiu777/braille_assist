"""Choose global or localized pen inference using web validation cases only."""
import json
from pathlib import Path

import cv2
import numpy as np
import torch

from pen_localizer import PenLocalizer
from roi_model import ROIClassifier
from train_roi import metrics

SPLIT=Path('data/openimage_pens/split_with_negatives.json')
OUTPUT=Path('runs/black_pen_active')
CANDIDATES=['runs/black_pen_expanded/best.pt','runs/black_pen_balanced/best.pt']
DETECTOR='runs/black_pen_localizer/weights/best.pt'


def cases(data,partition):
    rows=[dict(path=path,label=label,view='context') for path,label in data['splits'][partition]]
    for path,label in data['splits'][partition]:
        source=data['source_groups'].get(path)
        if label==2 and source:
            rows.append(dict(path=source['original_path'],label=2,view='full_scene'))
    return rows


def report_labels(predictions,truth):
    # Reuse the confusion-matrix implementation without recalibrating scores.
    values=np.array([[.99 if label==name else .001 for name in ('chips','soda','pens')] for label in predictions])
    result=metrics(values,np.array(truth),.5,.25)
    matrix=np.array(result['confusion_matrix'])
    totals=matrix.sum(1)
    recalls=np.diag(matrix)/np.maximum(totals,1)
    result['balanced_accuracy']=float(recalls[totals>0].mean())
    result['per_class_recall']=[float(value) if total else None for value,total in zip(recalls,totals)]
    if not totals[3]:
        result['negative_false_accept_rate']=None
    if not sum(totals[[0,1,3]]):
        result['pen_false_accept_rate']=None
    return result


def predict_cached(row,setting):
    if setting['mode']=='global':
        return row['global_label']
    valid=[item for item in row['proposals'] if item['detector_confidence']>=setting['detector_threshold']
           and item['label']=='pens' and item['scores'][2]>=setting['verification_threshold']]
    if valid:
        return 'pens'
    label=row['global_label']
    if label=='pens' and row['global_scores'][2]<setting['fallback_threshold']:
        return None
    return label


def candidate(checkpoint,validation,proposals,image_size):
        classifier=ROIClassifier(checkpoint)
        cached=[]
        for row,boxes in zip(validation,proposals):
            frame=cv2.imread(row['path']);height,width=frame.shape[:2]
            label,scores=classifier.predict(frame)
            item=dict(row,global_label=label,global_scores=scores.tolist(),proposals=[])
            for (x1,y1,x2,y2),confidence in boxes:
                padding=max(4,int(.12*max(x2-x1,y2-y1)))
                left,top,right,bottom=max(0,x1-padding),max(0,y1-padding),min(width,x2+padding),min(height,y2+padding)
                if right<=left or bottom<=top:continue
                crop_label,crop_scores=classifier.predict(frame[top:bottom,left:right])
                item['proposals'].append(dict(label=crop_label,scores=crop_scores.tolist(),detector_confidence=confidence))
            cached.append(item)
        settings=[dict(mode='global',image_size=image_size)]
        for detector in (.05,.1,.2,.35):
            for verification in (.85,.9,.95,.98):
                for fallback in (.99,.995,1.01):
                    settings.append(dict(mode='localized',image_size=image_size,detector_threshold=detector,
                                         verification_threshold=verification,fallback_threshold=fallback))
        best=None
        for setting in settings:
            predictions=[predict_cached(row,setting) for row in cached]
            result=report_labels(predictions,[row['label'] for row in cached])
            objective=result['balanced_accuracy']-.5*result['pen_false_accept_rate']
            key=(objective,-result['pen_false_accept_rate'],result['balanced_accuracy'],
                 setting['mode']=='global',setting.get('detector_threshold',0))
            if best is None or key>best['key']:
                best=dict(key=key,setting=setting,validation=result,checkpoint=checkpoint)
        print(checkpoint,best['setting'],best['validation'],flush=True)
        return best


def main():
    torch.set_num_threads(3)
    data=json.loads(SPLIT.read_text())
    validation=cases(data,'val')
    records=[];winner=None
    for image_size in (384,640):
        localizer=PenLocalizer(DETECTOR,.05,image_size)
        proposals=[]
        for row in validation:
            proposals.append(localizer.proposals(cv2.imread(row['path'])))
        for checkpoint in CANDIDATES:
            best=candidate(checkpoint,validation,proposals,image_size)
            records.append(best)
            if winner is None or best['key']>winner['key']:
                winner=best
    OUTPUT.mkdir(parents=True,exist_ok=True)
    saved=torch.load(winner['checkpoint'],map_location='cpu',weights_only=True)
    if winner['setting']['mode']=='localized':
        saved.update(pen_localizer=DETECTOR,
                     pen_localizer_image_size=winner['setting']['image_size'],
                     pen_detector_threshold=winner['setting']['detector_threshold'],
                     pen_verification_threshold=max(winner['setting']['verification_threshold'],
                        saved.get('class_thresholds',[saved['threshold']]*3)[2]),
                     pen_global_fallback_threshold=winner['setting']['fallback_threshold'])
    torch.save(saved,OUTPUT/'best.pt')
    (OUTPUT/'selection.json').write_text(json.dumps(dict(winner=winner,candidates=records,
        validation_cases=len(validation),policy='Web validation context and full-scene views only; no camera or test selection'),indent=2))
    (OUTPUT/'split.json').write_text(json.dumps(data,indent=2))
    print('Active candidate:',winner['checkpoint'],winner['setting'],flush=True)


if __name__=='__main__':
    main()
