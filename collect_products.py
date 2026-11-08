"""Download reviewed web-search candidates and retain provenance."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps

SOURCES = {
    "chips": [
        "https://cdn.shopify.com/s/files/1/0104/5741/5716/products/halssweetchili_2048x2048.jpg?v=1622785706",
        "https://storage.googleapis.com/images-prs-prd-c7e7986.prs.prd.v8.commerce.mi9cloud.com/product-images/zoom/00855036005396_1",
        "https://www.instacart.com/image-server/1200x1200/www.instacart.com/assets/domains/product-image/file/large_a081a5b4-0052-44b1-9a33-1a88c7eff699.jpg",
        "https://oasissnacks.com/cdn/shop/products/hals_chips_vis_sweetchili_3.png?v=1767210552",
        "https://images.heb.com/is/image/HEBGrocery/015862017-1",
        "https://superpharmstorage.blob.core.windows.net/hybris/products/desktop/large/855036005402.jpg",
        "https://objectstorage.us-ashburn-1.oraclecloud.com/n/idfzjtd1dz5w/b/storesgo-images/o/products/storesgo-hals-new-york-sweet-chili-kettle-cooked-potato-chips-94581.jpg",
        "https://s3.amazonaws.com/foodservicedirect.com/largeoriginalimages/10855036005393.jpg",
        "https://www.instacart.com/image-server/1200x1200/www.instacart.com/assets/domains/product-image/file/large_c5738036-cf91-492e-8588-ad1abe8831f8.png",
    ],
    "red_bull": [
        "https://www.kroger.com/product/images/xlarge/front/0061126999100",
        "https://medias.watsons.com.ph/publishing/WTCPH-50038569-front-zoom.jpg?version=1734136961",
        "https://i5.walmartimages.com/seo/Red-Bull-Energy-Drink-Can_f1e18ab5-c840-44f9-a411-5e5bb58f1bd1.c1e01a665efde7554c1f5189769d5c51.jpeg",
        "https://www.bestwaywholesale.co.uk/img/products/1000/9/90474019.jpg",
        "https://arizona.bevmo.com/cdn/shop/files/8a76e532-99a8-4a88-a124-e8f18886489c.png?v=1741032199",
        "https://www.myamericanshop.be/cdn/shop/files/red-bull-energy-drink-original-90162602-1144822501.png?v=1777328222",
        "https://www.fitnessvending.com/cdn/shop/files/red-bull-energy-drink-250ml-original_grande.jpg?v=1770002810",
        "https://westbids.co.uk/cdn/shop/files/Red-Bull-355ml.webp?v=1779029040",
        "https://cdn.shopify.com/s/files/1/0050/5466/0678/files/RedBull-OriginalEnergyDrink-24x250ml_c90f449b-0a6d-4c5f-9f51-cee43302fc51.webp?v=1722382568",
        "https://shop-envision.com/cdn/shop/files/Red-Bull-Energy-Drink-80mg-Caffeine-8-4-fl-oz-Can_c9e445f3-1800-40d0-81ab-6b93a9aaacb3.d2f8d87c58e8287c26cae8b3b6d9e38a.webp?v=1745594411",
    ],
}


def fingerprint(image):
    # Normalize white padding before comparing resized copies of catalog photos.
    box = ImageChops.difference(image, Image.new("RGB", image.size, "white")).convert("L").point(lambda p: 255 if p > 35 else 0).getbbox()
    image = image.crop(box) if box else image
    gray = np.asarray(image.convert("L").resize((9, 8)))
    return (gray[:, 1:] > gray[:, :-1]).flatten()


def fetch(item):
    name, index, url = item
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=25) as response:
            raw = response.read(15_000_000)
        with Image.open(BytesIO(raw)) as source:
            rgba = source.convert("RGBA")
            image = Image.new("RGBA", rgba.size, "white")
            image.alpha_composite(rgba)
            image = image.convert("RGB")
        if min(image.size) < 100:
            raise ValueError("Image too small")
        return name, index, url, image, None
    except Exception as error:
        return name, index, url, None, str(error)


def main():
    root = Path("data/web_products")
    root.mkdir(parents=True, exist_ok=True)
    manifest, accepted, hashes = [], [], {name: [] for name in SOURCES}
    items = [(name, i, url) for name, urls in SOURCES.items() for i, url in enumerate(urls)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, index, url, image, error in pool.map(fetch, items):
            row = {"class": name, "url": url, "error": error}
            # Manual contact-sheet review: these reuse another candidate's photo.
            if (name, index) in {("chips", 2), ("red_bull", 4)}:
                row["error"] = "Excluded after visual review: likely reused catalog photo"
                manifest.append(row)
                continue
            if image is not None:
                fp = fingerprint(image)
                duplicate = any(np.count_nonzero(fp != previous) <= 5 for previous in hashes[name])
                row["duplicate"] = duplicate
                if not duplicate:
                    hashes[name].append(fp)
                    path = root / name / f"web_{index:02}.jpg"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    image.thumbnail((1200, 1200))
                    image.save(path, quality=95)
                    row["path"] = str(path)
                    row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                    accepted.append((name, path, image.copy()))
            manifest.append(row)
            print(name, index, error or ("duplicate skipped" if row["duplicate"] else "accepted"), flush=True)
    (root / "sources.json").write_text(json.dumps(manifest, indent=2))
    sheet = Image.new("RGB", (1000, ((len(accepted) + 4) // 5) * 240), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (name, path, image) in enumerate(accepted):
        x, y = (i % 5) * 200, (i // 5) * 240
        sheet.paste(ImageOps.contain(image, (190, 210)), (x, y + 25))
        draw.text((x, y), f"{name}/{path.name}", fill="black")
    sheet.save(root / "review.jpg")
    print("Accepted counts:", {name: len(values) for name, values in hashes.items()})


if __name__ == "__main__":
    main()
