"""Review targeted pen photos and split whole product groups before training."""
import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path("data/rollerball_pens")


def main():
    data = json.loads(Path("data/pen_refinement/split.json").read_text())
    records = json.loads((ROOT/"sources.json").read_text())
    # Visual review of review.jpg: full pens, capped/uncapped views, desk views,
    # and in-hand photos. Product model groups stay entirely in one partition.
    approved = {"alexandria", "parker_meridian", "monroe", "sheaffer_100",
                "cross_edge", "aurora_ipsilon", "sheaffer_300", "sheaffer_vfm"}
    groups, hashes = {}, {}
    for split, rows in data["splits"].items():
        for path, _ in rows:
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                digest = hashlib.sha256(str(rgb.size).encode()+rgb.tobytes()).hexdigest()
            hashes[digest] = split
    accepted = []
    for row in records:
        if "error" in row or row["group"] not in approved:
            row["review"] = "Excluded: failed download or unavailable product photos"
            continue
        split = row["split"]
        group = row["group"]
        if group in groups and groups[group] != split:
            raise ValueError(f"Product group overlaps partitions: {group}")
        groups[group] = split
        with Image.open(row["path"]) as image:
            rgb = image.convert("RGB")
            digest = hashlib.sha256(str(rgb.size).encode()+rgb.tobytes()).hexdigest()
        if digest in hashes:
            row["review"] = "Excluded: duplicate decoded image"
            continue
        hashes[digest] = split
        data["splits"][split].append([row["path"], data["classes"].index("pens")])
        row["review"] = "Accepted after visual review; product group kept in one split"
        accepted.append(row)
    data["product_groups"] = groups
    data["reference"] = {"path": str(ROOT/"user_reference.jpg"),
                         "url": "https://img.magnific.com/free-psd/elegant-black-gold-luxury-rollerball-pen_84443-65689.jpg?semt=ais_hybrid&w=740&q=80",
                         "purpose": "User-requested reference check only; excluded from training and calibration"}
    (ROOT/"split.json").write_text(json.dumps(data, indent=2))
    (ROOT/"reviewed_sources.json").write_text(json.dumps(records, indent=2))
    print("New pen photos:", len(accepted), "groups:", groups)
    print("Split counts:", {split: len(rows) for split, rows in data["splits"].items()})


if __name__ == "__main__":
    main()
