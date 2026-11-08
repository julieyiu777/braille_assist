"""Train a one-class pen detector using the reviewed, source-grouped web photos."""
import json
from pathlib import Path
import shutil

import torch
from ultralytics import YOLO


def main():
    torch.set_num_threads(2)
    root=Path('data/black_pen_detection').resolve()
    split=json.loads(Path('data/openimage_pens/split_with_negatives.json').read_text())
    records=json.loads(Path('data/openimage_pens/reviewed_sources.json').read_text())
    accepted={row['path']:row for row in records if row.get('review','').startswith('Accepted:')}
    counts={}
    for partition,rows in split['splits'].items():
        images=root/'images'/partition;labels=root/'labels'/partition
        images.mkdir(parents=True,exist_ok=True);labels.mkdir(parents=True,exist_ok=True)
        count=0
        for path,label in rows:
            if path in accepted:
                row=accepted[path]
                name=row['upstream_split']+'_'+row['image_id']
                shutil.copyfile(row['original_path'],images/(name+'.jpg'))
                lines=[]
                for box in row['boxes']:
                    x1,x2,y1,y2=box['XMin'],box['XMax'],box['YMin'],box['YMax']
                    lines.append(f'0 {(x1+x2)/2:.7f} {(y1+y2)/2:.7f} {x2-x1:.7f} {y2-y1:.7f}')
                (labels/(name+'.txt')).write_text('\n'.join(lines)+'\n')
            elif label==3:
                # Reviewed negative crops; do not claim the entire original
                # photo is exhaustive for every possible background pen.
                name='negative_'+Path(path).stem
                shutil.copyfile(path,images/(name+'.jpg'))
                (labels/(name+'.txt')).write_text('')
            else:
                continue
            count+=1
        counts[partition]=count
    config=root/'data.yaml'
    config.write_text(f'path: {root}\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: pens\n')
    (root/'provenance.json').write_text(json.dumps(dict(source_split='data/openimage_pens/split_with_negatives.json',
        counts=counts,policy='Original image/source-photographer groups retained; no camera frames used'),indent=2))
    print('Detection split:',counts,flush=True)
    model=YOLO('yolov8n.pt')
    model.train(data=str(config),epochs=25,imgsz=384,batch=8,device='cpu',workers=0,
        project=str(Path('runs').resolve()),name='black_pen_localizer',exist_ok=False,
        pretrained=True,freeze=10,patience=7,plots=False,amp=False,seed=42,
        degrees=45,translate=.1,scale=.4,fliplr=.5,mosaic=.5,close_mosaic=5,
        optimizer='AdamW',lr0=.001,verbose=False)


if __name__=='__main__':
    main()
