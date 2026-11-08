"""Compare pen pipelines on fixed web partitions and the separate camera diagnostic."""
import json
from pathlib import Path

import cv2
import torch

from calibrate_black_pen_pipeline import cases,report_labels
from roi_model import ROIClassifier


def evaluate(classifier,rows):
    predictions=[]
    for row in rows:
        frame=cv2.imread(row['path'])
        if frame is None:
            raise ValueError('Cannot read evaluation image')
        label,scores,bounds=classifier.predict_localized(frame)
        predictions.append(dict(row,predicted=label,scores=scores.tolist(),
                                bounds=[int(value) for value in bounds] if bounds else None))
    return dict(metrics=report_labels([row['predicted'] for row in predictions],[row['label'] for row in predictions]),
                predictions=predictions)


def main():
    torch.set_num_threads(3)
    data=json.loads(Path('data/openimage_pens/split_with_negatives.json').read_text())
    camera_path=Path('data/openimage_pens/camera_diagnostic.json')
    camera=json.loads(camera_path.read_text()) if camera_path.exists() else None
    report={}
    for name,path in [('previous','runs/rollerball_active/best.pt'),('expanded','runs/black_pen_active/best.pt')]:
        classifier=ROIClassifier(path)
        report[name]={}
        for partition in ('val','test'):
            all_cases=cases(data,partition)
            context=[row for row in all_cases if row['view']=='context']
            full=[row for row in all_cases if row['view']=='full_scene']
            report[name][partition]=evaluate(classifier,context)
            report[name][partition+'_full_scenes']=evaluate(classifier,full)
        if camera:
            report[name]['camera_diagnostic']=evaluate(classifier,camera['frames'])
        print(name,{key:value['metrics'] for key,value in report[name].items()},flush=True)
    report['notes']=dict(camera=camera['purpose'] if camera else 'Private camera diagnostic not included',
        camera_labels=camera['label_source'] if camera else None,
        views='Context and full-scene evaluations share source photos; do not count views as independent samples.',
        selection='Web validation only; camera diagnostic and test reports do not select thresholds.')
    Path('runs/black_pen_active/comparison.json').write_text(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
