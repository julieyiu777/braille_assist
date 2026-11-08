"""Collect annotated Open Images pen photos and provenance for visual review."""
import csv
import hashlib
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import shutil
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageOps

ROOT = Path('data/openimage_pens')
PEN = '/m/0k1tl'


def fetch(url):
    return urlopen(Request(url, headers={'User-Agent':'MakeCU educational classifier data collection'}), timeout=40)


def cached(url, path):
    if not path.exists():
        temporary = path.with_suffix(path.suffix+'.download')
        with fetch(url) as source, temporary.open('wb') as target:
            shutil.copyfileobj(source, target)
        temporary.replace(path)
    return path


def collect_split(split):
    if split=='train':
        return collect_training_subset()
    boxes_path = cached(f'https://storage.googleapis.com/openimages/v5/{split}-annotations-bbox.csv', ROOT/f'{split}-boxes.csv')
    boxes = defaultdict(list)
    with boxes_path.open() as source:
        for row in csv.DictReader(source):
            if row['LabelName'] == PEN and row['Confidence']=='1' and row.get('IsDepiction','0')=='0' and row.get('IsGroupOf','0')=='0':
                boxes[row['ImageID']].append({key:float(row[key]) for key in ('XMin','XMax','YMin','YMax')})
    metadata_path = cached(f'https://storage.googleapis.com/openimages/2018_04/{split}/{split}-images-with-rotation.csv', ROOT/f'{split}-metadata.csv')
    metadata = {}
    with metadata_path.open() as source:
        for row in csv.DictReader(source):
            if row['ImageID'] in boxes:
                metadata[row['ImageID']] = row
    print(split, 'original photos with verified pen boxes:',len(boxes),flush=True)
    return [dict(image_id=identity, upstream_split=split, boxes=values,
                 metadata=metadata.get(identity,{}),
                 image_url=f'https://open-images-dataset.s3.amazonaws.com/{split}/{identity}.jpg',
                 source_page=metadata.get(identity,{}).get('OriginalLandingURL'),
                 annotation_url=f'https://storage.googleapis.com/openimages/v5/{split}-annotations-bbox.csv')
            for identity,values in boxes.items()]


def collect_training_subset(limit=160):
    selected_path=ROOT/'train-selected.json'
    if selected_path.exists():
        return json.loads(selected_path.read_text())
    boxes=defaultdict(list)
    url='https://storage.googleapis.com/openimages/v6/oidv6-train-annotations-bbox.csv'
    with fetch(url) as source:
        header=next(source).decode().strip().split(',')
        for line in source:
            if b',/m/0k1tl,' not in line:
                continue
            row=dict(zip(header,next(csv.reader([line.decode().strip()]))))
            if row['Confidence']!='1' or row.get('IsDepiction','0')!='0' or row.get('IsGroupOf','0')!='0':
                continue
            boxes[row['ImageID']].append({key:float(row[key]) for key in ('XMin','XMax','YMin','YMax')})
            if len(boxes)>=limit:
                break
    print('train selected pen originals:',len(boxes),flush=True)
    metadata={}
    metadata_url='https://storage.googleapis.com/openimages/2018_04/train/train-images-boxable-with-rotation.csv'
    import io
    with fetch(metadata_url) as response:
        for row in csv.DictReader(io.TextIOWrapper(response,encoding='utf-8')):
            if row['ImageID'] in boxes:
                metadata[row['ImageID']]=row
                if len(metadata)==len(boxes):
                    break
    rows=[dict(image_id=identity,upstream_split='train',boxes=values,metadata=metadata.get(identity,{}),
               image_url=f'https://open-images-dataset.s3.amazonaws.com/train/{identity}.jpg',
               source_page=metadata.get(identity,{}).get('OriginalLandingURL'),annotation_url=url)
          for identity,values in boxes.items()]
    selected_path.write_text(json.dumps(rows,indent=2))
    return rows


def download(row):
    identity, split = row['image_id'], row['upstream_split']
    path = ROOT/'originals'/f'{split}_{identity}.jpg'
    crop_path = ROOT/'crops'/f'{split}_{identity}.jpg'
    try:
        cached(row['image_url'],path)
        with Image.open(path) as source:
            image = source.convert('RGB')
        # One context crop per original. Keep all variants of this image in the
        # same partition; these are not independent extra photographs.
        box = max(row['boxes'],key=lambda b:(b['XMax']-b['XMin'])*(b['YMax']-b['YMin']))
        width,height = image.size
        x1,y1,x2,y2 = box['XMin']*width,box['YMin']*height,box['XMax']*width,box['YMax']*height
        if min(x2-x1,y2-y1)<8 or max(x2-x1,y2-y1)<45:
            raise ValueError('Pen box is too small for reliable training')
        padding = max(12, .15*max(x2-x1,y2-y1))
        bounds = (int(max(0,x1-padding)),int(max(0,y1-padding)),int(min(width,x2+padding)),int(min(height,y2+padding)))
        crop = image.crop(bounds)
        crop.thumbnail((1000,1000))
        crop.save(crop_path,quality=95)
        row.update(path=str(crop_path),original_path=str(path),crop_bounds=bounds,
                   source_group=f'openimages:{identity}',sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    except Exception as error:
        row['error']=str(error)
    return row


def main():
    for name in ('originals','crops'):
        (ROOT/name).mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        groups=list(pool.map(collect_split,('validation','test','train')))
    rows=[row for group in groups for row in group]
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows=list(pool.map(download,rows))
    (ROOT/'sources.json').write_text(json.dumps(rows,indent=2))
    good=[row for row in rows if 'error' not in row]
    for page in range((len(good)+79)//80):
        selected=good[page*80:(page+1)*80]
        sheet=Image.new('RGB',(1600,((len(selected)+9)//10)*160),'white')
        draw=ImageDraw.Draw(sheet)
        for local,row in enumerate(selected):
            x,y=local%10*160,local//10*160
            with Image.open(row['path']) as image:
                sheet.paste(ImageOps.contain(image,(155,130)),(x,y+28))
            draw.text((x,y),f'{page*80+local}: {row["image_id"][:10]}',fill='black')
            draw.text((x,y+12),row['upstream_split'],fill='black')
        sheet.save(ROOT/f'review_{page:02}.jpg')
    print('Downloaded usable originals:',len(good),'errors:',len(rows)-len(good),flush=True)


if __name__=='__main__':
    main()
