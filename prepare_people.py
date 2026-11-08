"""Create one annotated person crop per source image from COCO128."""
import json
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageOps

URL = "https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip"


def main():
    root = Path("data/web_products/person")
    root.mkdir(parents=True, exist_ok=True)
    archive = Path("data/coco128.zip")
    if not archive.exists():
        with urlopen(Request(URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as response:
            archive.write_bytes(response.read())
    records, previews = [], []
    with ZipFile(archive) as dataset:
        names = set(dataset.namelist())
        for label_path in sorted(names):
            if "/labels/train2017/" not in label_path or not label_path.endswith(".txt"):
                continue
            image_path = label_path.replace("/labels/", "/images/").replace(".txt", ".jpg")
            if image_path not in names:
                continue
            boxes = [list(map(float, line.split())) for line in dataset.read(label_path).decode().splitlines() if line.strip()]
            boxes = [box for box in boxes if box[0] == 0]
            if not boxes:
                continue
            with Image.open(BytesIO(dataset.read(image_path))) as image:
                image = image.convert("RGB")
                width, height = image.size
                boxes = [box for box in boxes if box[3] * width >= 64 and box[4] * height >= 96]
                if not boxes:
                    continue
                _, cx, cy, bw, bh = max(boxes, key=lambda box: box[3] * box[4])
                bounds = [max(0, int((cx-bw/2)*width)), max(0, int((cy-bh/2)*height)),
                          min(width, int((cx+bw/2)*width)), min(height, int((cy+bh/2)*height))]
                crop = image.crop(bounds)
            path = root / Path(image_path).name
            crop.save(path, quality=95)
            records.append({"path": str(path), "source_image": image_path,
                            "source_dataset": URL, "bbox_xyxy": bounds,
                            "class": "person", "annotation_class_id": 0})
            previews.append((path.name, crop))
            if len(records) == 18:
                break
    (root.parent / "person_sources.json").write_text(json.dumps(records, indent=2))
    sheet = Image.new("RGB", (900, ((len(previews)+5)//6)*210), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (name, crop) in enumerate(previews):
        x, y = i%6*150, i//6*210
        sheet.paste(ImageOps.contain(crop, (145, 180)), (x, y+20))
        draw.text((x, y), name, fill="black")
    sheet.save(root.parent / "person_review.jpg")
    print(f"Prepared {len(records)} person crops from distinct source images.")


if __name__ == "__main__":
    main()
