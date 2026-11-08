"""Download Commons refinement candidates with attribution for visual review."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageOps

ROOT = Path("data/pen_refinement")
QUERIES = [
    ("pens", 'intitle:"black pen"', 4),
    ("pens", 'intitle:"ballpoint" pen -macro -tip -patent -parts', 6),
    ("negative", 'intitle:"pencil" -drawing -art -pen -sharpener', 5),
    ("negative", 'intitle:"remote control" -diagram', 5),
    ("negative", 'intitle:"spoon" -food -dish -painting', 4),
    ("negative", 'intitle:"scissors" -drawing -diagram', 4),
    ("negative", 'intitle:"USB flash drive"', 4),
]
TITLES = {
    "pens": ["A black ballpen, India (2016).jpg", "Ballpoint on table.jpg", "Ballpoint.jpg"],
    "negative": ["Pencil.jpg", "Pencils.jpg", "Ruler.jpg", "Chopsticks.jpg", "Spoon.jpg",
                 "Toothbrush.jpg", "Scissors.jpg", "Eraser.jpg", "Key.jpg"],
}


def read_url(url):
    with urlopen(Request(url, headers={"User-Agent": "MakeCU-Training/1.0 (educational image classifier)"}), timeout=30) as response:
        return response.read(20_000_000)


def download(row):
    path = Path(row["path"])
    try:
        if not path.exists():
            with Image.open(BytesIO(read_url(row["url"]))) as source:
                image = source.convert("RGB")
                image.thumbnail((1000, 1000))
                path.parent.mkdir(parents=True, exist_ok=True)
                image.save(path, quality=95)
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception as error:
        row["error"] = str(error)
    print(row["title"], row.get("error", "downloaded"), flush=True)
    return row


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    rows, seen = [], set()
    requests = []
    for label, query, limit in QUERIES:
        params = dict(action="query", format="json", generator="search", gsrsearch=query,
                      gsrnamespace=6, gsrlimit=limit, prop="imageinfo",
                      iiprop="url|extmetadata|size", iiurlwidth=1000)
        requests.append((label, params))
    for label, titles in TITLES.items():
        requests.append((label, dict(action="query", format="json",
                                    titles="|".join("File:"+title for title in titles),
                                    prop="imageinfo", iiprop="url|extmetadata|size", iiurlwidth=1000)))
    for label, params in requests:
        data = json.loads(read_url("https://commons.wikimedia.org/w/api.php?"+urlencode(params)))
        for page in data.get("query", {}).get("pages", {}).values():
            title = page["title"]
            if "imageinfo" not in page or title in seen or not title.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            seen.add(title)
            info = page["imageinfo"][0]
            metadata = info.get("extmetadata", {})
            identity = hashlib.sha256(title.encode()).hexdigest()[:12]
            rows.append(dict(title=title, label=label, url=info.get("thumburl", info["url"]),
                             source_page=info["descriptionurl"],
                             path=str(ROOT/"candidates"/label/f"commons_{identity}.jpg"),
                             attribution={key: metadata.get(key, {}).get("value", "")
                                          for key in ("Artist", "LicenseShortName", "LicenseUrl", "ImageDescription")}))
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(download, rows))
    (ROOT/"sources.json").write_text(json.dumps(rows, indent=2))
    good = [row for row in rows if "error" not in row]
    sheet = Image.new("RGB", (1200, max(1, (len(good)+5)//6)*200), "white")
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(good):
        x, y = i%6*200, i//6*200
        with Image.open(row["path"]) as source:
            sheet.paste(ImageOps.contain(source, (190, 155)), (x, y+40))
        draw.text((x, y), f"{i}: {row['label']}", fill="black")
        draw.text((x, y+15), row["title"][5:30], fill="black")
    sheet.save(ROOT/"review.jpg")


if __name__ == "__main__":
    main()
