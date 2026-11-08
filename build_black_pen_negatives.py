"""Append visually reviewed non-pen photos without moving prior partitions."""
import json
from pathlib import Path

from build_black_pen_split import ROOT,perceptual_hash


def main():
    data=json.loads((ROOT/'split.json').read_text())
    rows=json.loads((ROOT/'negative_sources.json').read_text())
    hashes=[perceptual_hash(path) for values in data['splits'].values() for path,_ in values]
    group_split={}
    for split,values in data['splits'].items():
        for path,_ in values:
            if path in data['source_groups']:
                group_split[data['source_groups'][path]['split_group']]=split
    groups={};accepted=[]
    for row in rows:
        if 'error' in row:
            continue
        value=perceptual_hash(row['path'])
        if any((value^old).bit_count()<=3 for old in hashes):
            row['review']='Excluded: duplicate/near-duplicate'
            continue
        hashes.append(value)
        row['review']='Visually reviewed: non-pen object/background; no presented pen'
        author=row['metadata'].get('AuthorProfileURL') or row['metadata'].get('Author') or row['image_id']
        group='photographer:'+author
        row['split_group']=group
        groups.setdefault(group,[]).append(row)
        accepted.append(row)
    targets={'train':len(accepted)*.7,'val':len(accepted)*.15,'test':len(accepted)*.15}
    counts=dict.fromkeys(targets,0)
    for group,members in sorted(groups.items(),key=lambda item:(-len(item[1]),item[0])):
        split=group_split.get(group) or max(targets,key=lambda key:targets[key]-counts[key])
        counts[split]+=len(members)
        for row in members:
            row['split']=split
            data['splits'][split].append([row['path'],3])
            data['source_groups'][row['path']]=dict(split_group=group,source_page=row['source_page'],
                                                   original_path=row['original_path'],label='negative')
    data['review_summary']['added_negative_originals']=len(accepted)
    data['review_summary']['new_negative_split_counts']=counts
    (ROOT/'negative_reviewed_sources.json').write_text(json.dumps(rows,indent=2))
    (ROOT/'split_with_negatives.json').write_text(json.dumps(data,indent=2))
    print('New reviewed negatives:',len(accepted),counts)
    print('Split sizes:',{key:len(value) for key,value in data['splits'].items()})


if __name__=='__main__':
    main()
