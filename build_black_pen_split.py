"""Apply visual black-pen review and group source photographers before splitting."""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT=Path('data/openimage_pens')
# Reviewed context crops on review_00..03.jpg. Reject colorful-only pens,
# markers/highlighters, uncertain objects, and extreme tip-only macros.
APPROVED = {
    5,6,17,22,23,26,28,32,33,39,40,41,51,52,55,56,59,60,62,66,69,70,
    78,79,80,82,90,93,100,101,104,109,112,114,117,118,122,125,
    130,131,134,140,147,148,153,154,155,158,162,167,171,173,175,177,
    178,179,182,185,187,190,193,195,197,198,204,206,207,209,214,215,
    218,220,221,225,228,231,235,237,241,243,244,245,246,248,252,255,
    262,263,268,269,271,274,278,286,288,
}


def perceptual_hash(path):
    with Image.open(path) as source:
        gray=np.asarray(source.convert('L').resize((32,32)),dtype=np.float32)
    block=cv2.dct(gray)[:8,:8].flatten()[1:]
    return int(''.join('1' if value>np.median(block) else '0' for value in block),2)


def main():
    data=json.loads(Path('data/rollerball_pens/split.json').read_text())
    original_rows={split:list(rows) for split,rows in data['splits'].items()}
    rows=json.loads((ROOT/'sources.json').read_text())
    good=[row for row in rows if 'error' not in row]
    legacy_hashes=[perceptual_hash(path) for group in data['splits'].values() for path,_ in group]
    candidates=[]
    prior=[]
    for i,row in enumerate(good):
        row['review_index']=i
        if i not in APPROVED:
            row['review']='Excluded: color-only, marker, macro-only, or uncertain object/framing'
            continue
        hashes=[perceptual_hash(row['path']),perceptual_hash(row['original_path'])]
        if any((value^old).bit_count()<=3 for value in hashes for old in legacy_hashes):
            row['review']='Excluded: perceptual match to earlier dataset; preserve legacy partitions'
            continue
        match=None
        for old in prior:
            if row['metadata'].get('OriginalMD5') and row['metadata'].get('OriginalMD5')==old['metadata'].get('OriginalMD5'):
                match=old
                break
            if any((value^other).bit_count()<=3 for value in hashes for other in old['_hashes']):
                match=old
                break
        if match is not None:
            row['review']='Excluded: duplicate/near-duplicate of '+match['image_id']
            continue
        row['_hashes']=hashes
        row['review']='Accepted: black/dark-barrel pen or black-grip pen in a real scene'
        # Photographer grouping prevents related desk/pen photo sessions from
        # appearing on both sides of the evaluation boundary.
        author=row['metadata'].get('AuthorProfileURL') or row['metadata'].get('Author') or row['image_id']
        row['split_group']='photographer:'+author
        candidates.append(row)
        prior.append(row)
    groups={}
    for row in candidates:
        groups.setdefault(row['split_group'],[]).append(row)
    targets={'train':len(candidates)*.7,'val':len(candidates)*.15,'test':len(candidates)*.15}
    counts=dict.fromkeys(targets,0)
    metadata={}
    for group, members in sorted(groups.items(),key=lambda item:(-len(item[1]),item[0])):
        split=max(targets,key=lambda key:targets[key]-counts[key])
        counts[split]+=len(members)
        for row in members:
            row['split']=split
            data['splits'][split].append([row['path'],2])
            metadata[row['path']]=dict(original_path=row['original_path'],boxes=row['boxes'],
                                     split_group=group,source_page=row['source_page'])
    # No augmented view is counted as a separate source photo.
    data['training_views']={path:info for path,info in metadata.items()
                            if any(path==row[0] for row in data['splits']['train'])}
    data['source_groups']=metadata
    data['review_summary']=dict(added_pen_originals=len(candidates),new_source_groups=len(groups),
        new_split_counts=counts,reviewed_candidates=len(good),legacy_partitions_retained=True)
    assert all(data['splits'][key][:len(value)]==value for key,value in original_rows.items())
    for row in rows:
        row.pop('_hashes',None)
    (ROOT/'reviewed_sources.json').write_text(json.dumps(rows,indent=2))
    (ROOT/'split.json').write_text(json.dumps(data,indent=2))
    print(json.dumps(data['review_summary'],indent=2))
    print('Total split sizes:',{key:len(value) for key,value in data['splits'].items()})


if __name__=='__main__':
    main()
