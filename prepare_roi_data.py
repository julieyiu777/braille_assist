"""Assemble product positives and annotated people/background negatives."""
import json
import shutil
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageOps

from collect_products import fetch, fingerprint
import numpy as np

EXTRA = {
    "chips": [
        "https://i5.walmartimages.com/seo/Lay-s-Classic-Potato-Chips-8-oz-Bag-pack-of-2_f1852517-59f2-467f-b176-3ea1f2b098ae.a06a239111071c1595ab75cf3bf2323f.jpeg",
        "https://objectstorage.us-ashburn-1.oraclecloud.com/n/idfzjtd1dz5w/b/storesgo-images/o/products/storesgo-lays-classic-potato-chips-8-oz-plastic-bag-143484.jpg",
        "https://assets.caseys.com/asset/ede67e10-7d9c-412f-8c49-0dedaf77471c/400x400/2840077008_base.PNG",
        "https://storage.googleapis.com/images-hei-prd-1313031.hei.prd.v8.commerce.mi9cloud.com/product-images/detail/e30acbf2-5bd6-462a-a780-06598cc8798f.jpeg",
        "https://prod-spinneys-cdn-new.azureedge.net/media/cache/68/f2/68f2772c8ca71daaaecdebbe404f9e74.jpg",
    ],
    "soda": [
        "https://images.unsplash.com/photo-1580859297753-0b52fa0fc46e?auto=format&fit=max&w=1200&q=90",
        "https://images.unsplash.com/photo-1572470705533-f2ee03dc505d?auto=format&fit=max&w=1200&q=90",
        "https://images.unsplash.com/photo-1750187422743-2b36b1dd1855?auto=format&fit=max&w=1200&q=90",
        "https://cdn.prod.website-files.com/656f6178843c6d5b52c7c010/66afc06567830b2a316bd377_IMG_5432-2.jpg",
    ],
    "pens": [
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/7/72/Black_pen_with_red_background.jpg/960px-Black_pen_with_red_background.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/5/5f/Ballpoint_Pen_DS.jpg/960px-Ballpoint_Pen_DS.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a2/Grey_ballpoint.jpg/960px-Grey_ballpoint.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/e/e1/Jinhao_182_twist_action_ballpoint_pen.jpg/960px-Jinhao_182_twist_action_ballpoint_pen.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/9/98/MUJI_GEL_INK_BALLPONIT_PEN_KNOCK_TYPE_0.5_BLACK.jpg/960px-MUJI_GEL_INK_BALLPONIT_PEN_KNOCK_TYPE_0.5_BLACK.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/c/c1/ZEBRA_PEN_SARASA_CLIP_0.4_BLACK_INK.jpg/960px-ZEBRA_PEN_SARASA_CLIP_0.4_BLACK_INK.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/3/3a/Lamy_2000_fountain_pen-1.jpg/960px-Lamy_2000_fountain_pen-1.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/6/68/Furuta_seigo_Japanese_fountain_pen_collections.jpg/960px-Furuta_seigo_Japanese_fountain_pen_collections.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/e/ed/A_pen_writing_%22Example%22.jpg/960px-A_pen_writing_%22Example%22.jpg",
        "https://api.openverse.org/v1/images/aef5002d-11a9-45e9-930b-bb2f2d247bc8/thumb/",
        "https://api.openverse.org/v1/images/2b3460ad-dc93-47b1-951a-d3781a266a48/thumb/",
    ],
}

PEN_ATTRIBUTION = [
    {"source_page": "https://commons.wikimedia.org/wiki/File:Black_pen_with_red_background.jpg",
     "author": "Bommi Dharrshini OKC", "license": "CC BY-SA 4.0",
     "license_url": "https://creativecommons.org/licenses/by-sa/4.0"},
    {"source_page": "https://commons.wikimedia.org/wiki/File:Ballpoint_Pen_DS.jpg",
     "author": "Augustus Binu", "license": "CC BY-SA 3.0",
     "license_url": "https://creativecommons.org/licenses/by-sa/3.0"},
    {"source_page": "https://commons.wikimedia.org/wiki/File:Grey_ballpoint.jpg",
     "author": "Fiersel", "license": "CC0",
     "license_url": "https://creativecommons.org/publicdomain/zero/1.0/"},
    {"source_page": "https://commons.wikimedia.org/wiki/File:Jinhao_182_twist_action_ballpoint_pen.jpg",
     "author": "Francis Flinch", "license": "CC BY-SA 4.0",
     "license_url": "https://creativecommons.org/licenses/by-sa/4.0"},
    {"source_page": "https://commons.wikimedia.org/wiki/File:MUJI_GEL_INK_BALLPONIT_PEN_KNOCK_TYPE_0.5_BLACK.jpg",
     "author": "Dinkun Chen", "license": "CC BY-SA 4.0",
     "license_url": "https://creativecommons.org/licenses/by-sa/4.0"},
    {"source_page": "https://commons.wikimedia.org/wiki/File:ZEBRA_PEN_SARASA_CLIP_0.4_BLACK_INK.jpg",
     "author": "Dinkun Chen", "license": "CC BY-SA 4.0",
     "license_url": "https://creativecommons.org/licenses/by-sa/4.0"},
    {"source_page": "https://commons.wikimedia.org/wiki/File:Lamy_2000_fountain_pen-1.jpg",
     "author": "Vincent Ignace", "license": "CC BY-SA 3.0",
     "license_url": "https://creativecommons.org/licenses/by-sa/3.0"},
    {"source_page": "https://commons.wikimedia.org/wiki/File:Furuta_seigo_Japanese_fountain_pen_collections.jpg",
     "author": "P1898", "license": "CC BY 4.0",
     "license_url": "https://creativecommons.org/licenses/by/4.0"},
    {"source_page": "https://commons.wikimedia.org/wiki/File:A_pen_writing_%22Example%22.jpg",
     "author": "Jiafei Slay Queen", "license": "CC0",
     "license_url": "https://creativecommons.org/publicdomain/zero/1.0/"},
    {"source_page": "https://www.flickr.com/photos/163837264@N07/42947527210",
     "author": "Semtrio", "license": "CC BY 2.0",
     "license_url": "https://creativecommons.org/licenses/by/2.0/"},
    {"source_page": "https://www.flickr.com/photos/137455566@N08/25358762843",
     "author": "FreePhoto.cc", "license": "CC0 1.0",
     "license_url": "https://creativecommons.org/publicdomain/zero/1.0/"},
]


def main():
    root = Path("data/roi_targets")
    records, previews = [], []
    for name in ("chips", "soda", "pens", "negative"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ("chips", "soda", "pens"):
        hashes = []
        source_name = "red_bull" if name == "soda" else name
        for source in sorted((Path("data/web_products") / source_name).glob("*.jpg")):
            dest = root / name / source.name
            shutil.copyfile(source, dest)
            with Image.open(source) as image:
                hashes.append(fingerprint(image.convert("RGB")))
            records.append({"path": str(dest), "source": str(source), "class": name})
        for i, url in enumerate(EXTRA[name]):
            _, _, _, image, error = fetch((name, i, url))
            if error:
                records.append({"url": url, "error": error})
                print(name, i, error, flush=True)
                continue
            fp = fingerprint(image)
            if any(np.count_nonzero(fp != previous) <= 5 for previous in hashes):
                records.append({"url": url, "excluded": "perceptual duplicate"})
                continue
            hashes.append(fp)
            path = root / name / f"extra_{i:02}.jpg"
            image.thumbnail((1200, 1200))
            image.save(path, quality=95)
            record = {"path": str(path), "url": url, "class": name}
            if name == "pens":
                record.update(PEN_ATTRIBUTION[i])
            records.append(record)
            previews.append((str(path.relative_to(root)), image.copy()))
            print(name, i, "accepted", flush=True)
    person_ids = set()
    for source in sorted(Path("data/web_products/person").glob("*.jpg")):
        dest = root / "negative" / f"person_{source.name}"
        shutil.copyfile(source, dest)
        person_ids.add(source.stem)
        records.append({"path": str(dest), "source": str(source), "class": "negative"})
    count = 0
    with ZipFile("data/coco128.zip") as archive:
        for source in sorted(archive.namelist()):
            if "/images/train2017/" not in source or not source.endswith(".jpg"):
                continue
            if Path(source).stem in person_ids:
                continue
            with Image.open(BytesIO(archive.read(source))) as image:
                image = image.convert("RGB")
                image.thumbnail((900, 900))
                dest = root / "negative" / f"scene_{Path(source).name}"
                image.save(dest, quality=95)
                previews.append((str(dest.relative_to(root)), image.copy()))
            records.append({"path": str(dest), "source": f"coco128.zip/{source}", "class": "negative"})
            count += 1
            if count == 24:
                break
    (root / "sources.json").write_text(json.dumps(records, indent=2))
    sheet = Image.new("RGB", (1000, ((len(previews)+5)//6)*180), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (name, image) in enumerate(previews):
        x,y = i%6*166, i//6*180
        sheet.paste(ImageOps.contain(image, (160,150)), (x,y+24))
        draw.text((x,y), name, fill="black")
    sheet.save(root / "review.jpg")
    print({name: len(list((root/name).glob("*.jpg")))
           for name in ("chips", "soda", "pens", "negative")})


if __name__ == "__main__":
    main()
