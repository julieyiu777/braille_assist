"""Add reviewed Hal's kettle-chip image-search results to the product data."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps

from collect_products import fetch, fingerprint

URLS = [
    "https://fig-product-images.s3.amazonaws.com/0855036005358.53017.1721333278843.front.png",
    "https://stat.ameba.jp/user_images/20210826/05/lachesis423/fa/90/j/o0490065314991949758.jpg",
    "https://doordash-static.s3.amazonaws.com/media/photosV2/fbe58cb7-3d91-412b-a20d-65c74267501a-retina-large.png",
    "https://doordash-static.s3.amazonaws.com/media/photosV2/770429a8-8d28-4d29-9828-09dc528307db-retina-large.png",
    "https://seasonskosher.com/api/content/images/thumbs/0452317_hals-hals-barbecue-chips-2-oz.jpeg",
    "https://d2lnr5mha7bycj.cloudfront.net/product-image/file/large_d1e28af8-e88f-4860-a51f-07076fe67ad1.png",
    "https://static.wixstatic.com/media/098a88_a3703ec1bfe74063aacbbc6fad60f133~mv2.png",
    "https://i5.walmartimages.com/seo/Hals-New-York-Sour-Cream-Onion-Chips-5-oz-Pack-Of-12_b719da3e-b8ca-40b6-803b-8a76566fb326.2889b3230d57db105d87762a43e25052.jpeg",
    "https://i5.walmartimages.com/seo/Hal-s-NY-Kettle-Cooked-Potato-Chips-Sour-Cream-Onion-2-oz-Bags-Pack-of-24_afdddb97-12c2-4bbc-bd49-fa1c471b8eb7.32a7afaf2a832de5992db20ab07c8628.jpeg",
    "https://d2lnr5mha7bycj.cloudfront.net/product-image/file/large_8350fc85-24ab-4054-b889-538005f91c57.png",
    "https://www.baristaunderground.com/cdn/shop/files/02915716-571e-4430-9835-0154a6c120ea_1445x.png?v=1690506183",
    "https://martie.com/cdn/shop/files/27oKgPUogiTvtdS9gP5UAibW0qIz7kA4R6VC6Vld37k_1024x1024.png?v=1705302986",
    "https://www.instacart.com/image-server/1200x1200/www.instacart.com/assets/domains/product-image/file/large_3655b172-a671-4252-b0df-3454139d26a2.jpg",
]


def trim_catalog_padding(image):
    white = np.asarray(image).min(2) > 235
    border = np.concatenate([white[0],white[-1],white[:,0],white[:,-1]])
    if border.mean() > .95:
        mask = ImageChops.difference(image,Image.new("RGB",image.size,"white")).convert("L").point(lambda value:255 if value>30 else 0)
        bounds = mask.getbbox()
        if bounds:
            return ImageOps.expand(image.crop(bounds),border=12,fill="white")
    return image


def main():
    root = Path("data/roi_targets")
    existing = [path for path in sorted((root/"chips").glob("*.jpg")) if not path.name.startswith("hals_")]
    hashes = []
    for path in existing:
        with Image.open(path) as image:
            hashes.append(fingerprint(image.convert("RGB")))
    rows,previews = [],[]
    with ThreadPoolExecutor(max_workers=5) as pool:
        for _,index,url,image,error in pool.map(fetch,[("chips",i,url) for i,url in enumerate(URLS)]):
            row = {"url":url,"class":"chips","error":error}
            if image is not None:
                image = trim_catalog_padding(image)
                fp = fingerprint(image)
                duplicate = any(np.count_nonzero(fp != old) <= 5 for old in hashes)
                row["duplicate"] = duplicate
                if not duplicate:
                    hashes.append(fp)
                    path = root/"chips"/f"hals_{index:02}.jpg"
                    image.thumbnail((1200,1200))
                    image.save(path,quality=95)
                    row["path"] = str(path)
                    previews.append((path.name,image.copy()))
            rows.append(row)
            print(index,error or ("duplicate skipped" if row["duplicate"] else "accepted"),flush=True)
    (root/"hals_sources.json").write_text(json.dumps(rows,indent=2))
    sheet = Image.new("RGB",(1000,max(1,(len(previews)+4)//5)*260),"white")
    draw = ImageDraw.Draw(sheet)
    for i,(name,image) in enumerate(previews):
        x,y = i%5*200,i//5*260
        sheet.paste(ImageOps.contain(image,(190,230)),(x,y+25))
        draw.text((x,y),name,fill="black")
    sheet.save(root/"hals_review.jpg")
    print("Total chips images:",len(list((root/"chips").glob("*.jpg"))))


if __name__ == "__main__":
    main()
