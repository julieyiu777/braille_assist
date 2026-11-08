"""Apply visual review decisions and preserve the original held-out photos."""
import json
from pathlib import Path

ROOT = Path("data/pen_refinement")
REVIEWED = {
    "File:Black pen.jpg": "train",
    "File:Uncapped black pen.jpg": "train",
    "File:Berol Signature P51 England Ballpens Ballpoint pen Roll pens Gel pens Schreibstift 01.jpg": "train",
    "File:A black ballpen, India (2016).jpg": "train",
    "File:Parker Vector Stainless Steel Ballpoint Pen.jpg": "val",
    "File:DKG Sacks Rochester promotional ballpoint pen with contact details.jpg": "test",
    "File:Apple Pencil OVP.JPG": "train",
    "File:Hama universal remote control, Kleiwegkwartier, Rotterdam (2021) 06.jpg": "train",
    "File:Sanyo IR remote control details 10.jpg": "train",
    "File:Kingston Technology DataTraveler G4 USB flash drive USB 3.0 32 Gb.jpg": "train",
    "File:Key.jpg": "train",
    "File:Ruler.jpg": "train",
    "File:Modern pencil (FindID 770956).jpg": "val",
    "File:Variety of scissors.jpg": "val",
    "File:Spoon.jpg": "val",
    "File:Toshiba CT-8543 remote control, Winschoten (2020) 05.jpg": "test",
    "File:USB Flash Drive Chapped.jpg": "test",
    "File:Pencil.jpg": "test",
}


def main():
    baseline = Path("runs/fullscreen_targets/split.json")
    data = json.loads(baseline.read_text())
    # Keep the collection and wide desk photo on disk, but do not teach the
    # classifier that a whole desk/background is itself a pen.
    excluded = {"data/roi_targets/pens/extra_07.jpg", "data/roi_targets/pens/extra_09.jpg"}
    data["splits"]["train"] = [row for row in data["splits"]["train"] if row[0] not in excluded]
    manifest = json.loads((ROOT/"sources.json").read_text())
    accepted = set()
    for row in manifest:
        title = row["title"]
        if title in REVIEWED:
            if "error" in row:
                raise ValueError(f"Selected photo was not downloaded: {title}")
            split = REVIEWED[title]
            label = data["classes"].index(row["label"])
            data["splits"][split].append([row["path"], label])
            row["review"] = f"Accepted after visual review; {split} only"
            accepted.add(title)
        else:
            row["review"] = "Excluded: unrelated search result, duplicate source, macro-only view, illustration, or unsuitable framing"
    assert accepted == set(REVIEWED), set(REVIEWED)-accepted
    data["review"] = {"baseline_split": str(baseline), "excluded_training_images": sorted(excluded),
                      "policy": "Original validation/test photos retained; new original photos assigned before training."}
    (ROOT/"split.json").write_text(json.dumps(data, indent=2))
    (ROOT/"reviewed_sources.json").write_text(json.dumps(manifest, indent=2))
    print({split: len(rows) for split, rows in data["splits"].items()})


if __name__ == "__main__":
    main()
