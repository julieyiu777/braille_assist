"""Collect targeted black/gold pen photo candidates, grouped by pen model."""
import hashlib
import html
import json
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageOps

ROOT = Path("data/rollerball_pens")
REFERENCE = "https://img.magnific.com/free-psd/elegant-black-gold-luxury-rollerball-pen_84443-65689.jpg?semt=ais_hybrid&w=740&q=80"
SOURCES = [
    ("alexandria", "train", "https://www.dayspringpens.com/products/alexandria-rollerball-pen-black"),
    ("sheaffer_sagaris", "train", "https://sheaffer.com/products/sheaffer%C2%AE-sagaris-9471-gloss-black-rollerball-pen-with-gold-tone-trim"),
    ("sheaffer_balance", "train", "https://www.truphaeinc.com/products/sheaffer-balance-ii-rollerball-pen-black-and-gold"),
    ("parker_ingenuity", "train", "https://www.parkerpen.com/writing-types/collections/ingenuity/ingenuity-rollerball-pen/SAP_2182007.html"),
    ("parker_meridian", "train", "https://www.penworld.eu/pen/parker/parker-meridian/parker-meridian-black-gold-trim-rollerball.html"),
    ("monroe", "train", "https://www.dayspringpens.com/products/monroe-ballpoint-pen-black"),
    ("sheaffer_100", "val", "https://www.dromgooles.com/products/sheaffer-100-rollerball-black-with-gold"),
    ("waterman_expert", "val", "https://goldspot.com/products/waterman-rollerball-pen-expert-new-generation-black-gt"),
    ("waterman_hemisphere", "test", "https://www.dayspringpens.com/products/waterman-hemisphere-black-gold-trim-rollerball-pen"),
    ("cross_edge", "test", "https://www.dayspringpens.com/products/cross-edge-gel-rollerball-pen-matte-black-lacquer-with-gold-trim"),
    ("aurora_ipsilon", "test", "https://www.pianki.com/aurora-ipsilon-deluxe-black-rollerball-pen--gold-plated-trim_p2699.html"),
    ("parker_aster", "train", "https://penz.co.in/products/parker-aster-black-gold-deluxe-gold-trim-rollerball-pen"),
]
DIRECT_IMAGES = {
    "alexandria": ["https://www.dayspringpens.com/cdn/shop/files/GP-1474-001.jpg?v=1753736329&width=1200",
                   "https://www.dayspringpens.com/cdn/shop/files/GP-1474-002.jpg?v=1753736329&width=1200",
                   "https://www.dayspringpens.com/cdn/shop/files/GP-1474_Alexandria_Black_Rollerball_Lifestyle_Image.jpg?v=1658242783&width=1200"],
    "monroe": ["https://www.dayspringpens.com/cdn/shop/files/GP-1316-001.jpg?v=1753736415&width=1200",
               "https://www.dayspringpens.com/cdn/shop/files/GP-1316-002.jpg?v=1762963666&width=1200",
               "https://www.dayspringpens.com/cdn/shop/files/GP-1316_Monroe_Black_Ballpoint_Lifestyle_Image.jpg?v=1658325241&width=1200"],
    "cross_edge": ["https://www.dayspringpens.com/cdn/shop/files/GP-1642-AT0555-20-001.jpg?v=1776096170&width=1200",
                   "https://www.dayspringpens.com/cdn/shop/files/GP-1642-AT0555-20-002.jpg?v=1776096170&width=1200"],
    "sheaffer_100": ["https://www.dromgooles.com/cdn/shop/files/Sheaffer100Rollerball-BlackwithGold.jpg?v=1697471571&width=1200",
                     "https://www.dromgooles.com/cdn/shop/files/Sheaffer100FountainPen-BlackwithGold2_0d49cbde-7b2f-4c22-a49e-78305d49a6ae.jpg?v=1697471570&width=1200",
                     "https://sheaffer.ph/cdn/shop/files/SE1934051_600x.jpg?v=1757518664",
                     "https://sheaffer.ph/cdn/shop/files/SE1931351_600x.jpg?v=1757518705"],
    "sheaffer_300": ["https://sheaffer.ph/cdn/shop/files/SE1932551_600x.jpg?v=1757518698",
                     "https://sheaffer.ph/cdn/shop/files/SE1931251_600x.jpg?v=1757518696",
                     "https://sheaffer.ph/cdn/shop/files/SE1934351_600x.jpg?v=1757518695"],
    "sheaffer_vfm": ["https://sheaffer.ph/cdn/shop/files/SE1940551_600x.jpg?v=1757518719"],
}
SOURCES.extend([("sheaffer_300", "train", "https://sheaffer.ph/collections/rollerball"),
                ("sheaffer_vfm", "train", "https://sheaffer.ph/collections/rollerball")])


def fetch(url):
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as response:
        return response.read(20_000_000)


class ProductMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("property", values.get("name")) in ("og:image", "twitter:image"):
            if values.get("content"):
                self.images.append(values["content"])


def collect(source):
    group, split, page = source
    try:
        parts = urlsplit(page)
        if group in DIRECT_IMAGES:
            images = DIRECT_IMAGES[group]
        elif "/products/" in parts.path:
            slug = parts.path.split("/products/")[-1]
            api = urlunsplit((parts.scheme, parts.netloc, "/products/"+slug+".js", "", ""))
            try:
                product = json.loads(fetch(api))
                images = product["images"][:8]
            except Exception:
                parser = ProductMeta()
                parser.feed(fetch(page).decode("utf-8", errors="replace"))
                images = parser.images[:1]
        else:
            parser = ProductMeta()
            parser.feed(fetch(page).decode("utf-8", errors="replace"))
            images = parser.images[:1]
        rows = []
        for i, url in enumerate(dict.fromkeys(images)):
            if url.startswith("//"):
                url = "https:"+url
            path = ROOT/"candidates"/f"{group}_{i:02}.jpg"
            source_page = "https://sheaffer.ph/collections/rollerball" if "sheaffer.ph/" in url else page
            row = dict(group=group, split=split, source_page=source_page, url=html.unescape(url),
                       path=str(path), license="Product photography; ownership retained by source; no open license asserted")
            try:
                if not path.exists():
                    with Image.open(BytesIO(fetch(row["url"]))) as original:
                        image = Image.new("RGBA", original.size, "white")
                        image.alpha_composite(original.convert("RGBA"))
                        image = image.convert("RGB")
                        image.thumbnail((1200, 1200))
                        image.save(path, quality=95)
                row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            except Exception as error:
                row["error"] = str(error)
            rows.append(row)
        print(group, len(rows), "candidates", flush=True)
        return rows or [dict(group=group, source_page=page, error="No product image metadata")]
    except Exception as error:
        print(group, str(error), flush=True)
        return [dict(group=group, source_page=page, error=str(error))]


def main():
    (ROOT/"candidates").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = [row for group in pool.map(collect, SOURCES) for row in group]
    (ROOT/"sources.json").write_text(json.dumps(rows, indent=2))
    try:
        raw = fetch(REFERENCE)
        with Image.open(BytesIO(raw)) as source:
            source.convert("RGB").save(ROOT/"user_reference.jpg", quality=95)
    except Exception as error:
        print("Reference:", error, flush=True)
    good = [row for row in rows if "error" not in row]
    sheet = Image.new("RGB", (1200, max(1, (len(good)+5)//6)*210), "white")
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(good):
        x, y = i%6*200, i//6*210
        with Image.open(row["path"]) as image:
            sheet.paste(ImageOps.contain(image, (190, 170)), (x, y+35))
        draw.text((x, y), f"{i}: {Path(row['path']).stem}", fill="black")
        draw.text((x, y+15), row["split"], fill="black")
    sheet.save(ROOT/"review.jpg")
    print("Downloaded candidates:", len(good), flush=True)


if __name__ == "__main__":
    main()
