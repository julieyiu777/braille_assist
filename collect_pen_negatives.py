"""Collect independently annotated look-alike objects for black-pen rejection."""
import csv
import json
from pathlib import Path

from collect_openimage_pens import ROOT,cached,download,fetch
from concurrent.futures import ThreadPoolExecutor
from PIL import Image,ImageDraw,ImageOps

NAMES={'Remote control','Mobile phone','Knife','Screwdriver','Toothbrush','Spoon','Scissors','Lipstick','Sunglasses'}


def main():
    classpath=cached('https://storage.googleapis.com/openimages/v5/class-descriptions-boxable.csv',ROOT/'classes.csv')
    classes={key:name for key,name in csv.reader(classpath.open()) if name in NAMES}
    positives={row['image_id'] for row in json.loads((ROOT/'sources.json').read_text())}
    used=set(positives); selected=[]; counts=dict.fromkeys(classes,0)
    for split in ('validation','test'):
        boxes={}
        with (ROOT/f'{split}-boxes.csv').open() as source:
            for row in csv.DictReader(source):
                key=row['LabelName'];identity=row['ImageID']
                if key not in classes or counts[key]>=8 or identity in used or row.get('IsDepiction','0')!='0' or row.get('IsGroupOf','0')!='0':
                    continue
                box={name:float(row[name]) for name in ('XMin','XMax','YMin','YMax')}
                if min(box['XMax']-box['XMin'],box['YMax']-box['YMin'])<.025:
                    continue
                used.add(identity);counts[key]+=1
                boxes[identity]=dict(image_id=identity,upstream_split=split,boxes=[box],
                    object_class=classes[key],image_url=f'https://open-images-dataset.s3.amazonaws.com/{split}/{identity}.jpg',
                    annotation_url=f'https://storage.googleapis.com/openimages/v5/{split}-annotations-bbox.csv')
        with (ROOT/f'{split}-metadata.csv').open() as source:
            for row in csv.DictReader(source):
                if row['ImageID'] in boxes:
                    boxes[row['ImageID']]['metadata']=row
                    boxes[row['ImageID']]['source_page']=row.get('OriginalLandingURL')
        selected.extend(boxes.values())
    with ThreadPoolExecutor(max_workers=5) as pool:
        records=list(pool.map(download,selected))
    (ROOT/'negative_sources.json').write_text(json.dumps(records,indent=2))
    good=[row for row in records if 'error' not in row]
    sheet=Image.new('RGB',(1600,((len(good)+9)//10)*160),'white');draw=ImageDraw.Draw(sheet)
    for i,row in enumerate(good):
        x,y=i%10*160,i//10*160
        with Image.open(row['path']) as image:
            sheet.paste(ImageOps.contain(image,(155,130)),(x,y+25))
        draw.text((x,y),f'{i}: {row["object_class"]}',fill='black')
    sheet.save(ROOT/'negative_review.jpg')
    print('Negative candidates:',len(good),{classes[key]:count for key,count in counts.items()},flush=True)


if __name__=='__main__':
    main()
