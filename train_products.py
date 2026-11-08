"""Compare a fine-tuned CNN and a pixel-based linear product classifier."""

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path
from urllib.request import Request, urlopen

import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms
from torchvision.models import ResNet18_Weights, resnet18

from detect import REFERENCE_IMAGES


class ProductImages(Dataset):
    def __init__(self, rows, transform):
        self.rows = rows
        self.transform = transform

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        path, label = self.rows[index]
        with Image.open(path) as image:
            return self.transform(image.convert("RGB")), label


def split_images(root, classes, seed):
    """Split original images before applying training-only augmentation."""
    splits = {name: [] for name in ("train", "val", "test")}
    seen = {}
    rng = random.Random(seed)
    for label, name in enumerate(classes):
        paths = []
        for path in sorted((root / name).glob("*")):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                digest = hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).hexdigest()
            if digest in seen:
                if seen[digest] != label:
                    raise ValueError(f"Identical image has conflicting labels: {path}")
                continue
            seen[digest] = label
            paths.append(path)
        if len(paths) < 3:
            raise ValueError(
                f"{name}: found {len(paths)} unique images; need at least 3 independent "
                "photos to populate train/val/test. Add photos with different angles, "
                "backgrounds and lighting. Do not duplicate or augment one photo to "
                "fill separate splits. Aim for 30+ photos per class."
            )
        rng.shuffle(paths)
        held_out = max(1, round(len(paths) * 0.15))
        partitions = {
            "test": paths[:held_out],
            "val": paths[held_out:2 * held_out],
            "train": paths[2 * held_out:],
        }
        for split, files in partitions.items():
            splits[split].extend((str(path), label) for path in files)
    return splits


def evaluate(model, loader, class_count):
    model.eval()
    confusion = torch.zeros(class_count, class_count, dtype=torch.int64)
    loss_sum = 0.0
    with torch.no_grad():
        for inputs, targets in loader:
            logits = model(inputs)
            loss_sum += nn.functional.cross_entropy(logits, targets, reduction="sum").item()
            for actual, predicted in zip(targets, logits.argmax(1)):
                confusion[actual, predicted] += 1
    return {
        "loss": loss_sum / len(loader.dataset),
        "accuracy": confusion.diag().sum().item() / len(loader.dataset),
        "confusion_matrix": confusion.tolist(),
        "per_class_recall": (
            confusion.diag() / confusion.sum(1).clamp_min(1)
        ).tolist(),
    }


def train_model(kind, splits, classes, args):
    torch.manual_seed(args.seed)
    if kind == "cnn":
        weights = ResNet18_Weights.DEFAULT
        model = resnet18(weights=weights)
        for parameter in model.parameters():
            parameter.requires_grad = False
        for parameter in model.layer4.parameters():
            parameter.requires_grad = True
        model.fc = nn.Linear(model.fc.in_features, len(classes))
        evaluation_transform = weights.transforms()
        training_transform = transforms.Compose([
            transforms.RandomResizedCrop(224, scale=(0.75, 1.0)),
            transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.1),
            evaluation_transform,
        ])
        optimizer = torch.optim.AdamW(
            filter(lambda p: p.requires_grad, model.parameters()), lr=0.0001
        )
    else:
        model = nn.Sequential(nn.Flatten(), nn.Linear(3 * 32 * 32, len(classes)))
        evaluation_transform = transforms.Compose([
            transforms.Resize((32, 32)), transforms.ToTensor(),
            transforms.Normalize((0.5,) * 3, (0.5,) * 3),
        ])
        training_transform = evaluation_transform
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)

    loaders = {
        split: DataLoader(
            ProductImages(rows, training_transform if split == "train" else evaluation_transform),
            batch_size=args.batch_size, shuffle=split == "train",
        )
        for split, rows in splits.items()
    }
    best_loss = float("inf")
    checkpoint = args.output / f"{kind}.pt"
    best_epoch = 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        if kind == "cnn":
            # Keep pretrained batch-normalization statistics fixed for small datasets.
            for module in model.modules():
                if isinstance(module, nn.BatchNorm2d):
                    module.eval()
        for inputs, targets in loaders["train"]:
            optimizer.zero_grad()
            loss = nn.functional.cross_entropy(model(inputs), targets)
            loss.backward()
            optimizer.step()
        validation = evaluate(model, loaders["val"], len(classes))
        print(f"{kind} epoch {epoch}: val accuracy={validation['accuracy']:.3f}, "
              f"val loss={validation['loss']:.4f}", flush=True)
        if validation["loss"] < best_loss:
            best_loss, best_epoch = validation["loss"], epoch
            torch.save({"state_dict": model.state_dict(), "classes": classes,
                        "kind": kind, "epoch": epoch}, checkpoint)
    model.load_state_dict(torch.load(checkpoint, weights_only=True)["state_dict"])
    return {
        "best_epoch": best_epoch,
        "validation": evaluate(model, loaders["val"], len(classes)),
        "test": evaluate(model, loaders["test"], len(classes)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/web_products"))
    parser.add_argument("--output", type=Path, default=Path("runs/products_three"))
    parser.add_argument("--classes", nargs="+", choices=[*REFERENCE_IMAGES, "person"],
                        default=["person", "chips", "red_bull"])
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--download-references", action="store_true")
    args = parser.parse_args()
    if args.epochs < 1 or args.batch_size < 1 or len(set(args.classes)) < 2:
        parser.error("Use positive epochs/batch size and at least two distinct classes.")
    torch.set_num_threads(4)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    for name in args.classes:
        folder = args.data / name
        folder.mkdir(parents=True, exist_ok=True)
        if name == "person":
            continue
        if args.download_references and name == "chips":
            print("Skipping chips reference: existing URL shows barbecue, not sweet chili. "
                  "Add your sweet chili photos manually.", flush=True)
            continue
        if args.download_references and not (folder / "reference.jpg").exists():
            request = Request(REFERENCE_IMAGES[name], headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=30) as response:
                with Image.open(response) as image:
                    image.convert("RGB").save(folder / "reference.jpg")
            print(f"Downloaded reference: {name}", flush=True)
    try:
        splits = split_images(args.data, args.classes, args.seed)
    except ValueError as error:
        parser.exit(2, f"Cannot train: {error}\n")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"seed": args.seed, "classes": args.classes, "splits": splits}
    (args.output / "split.json").write_text(json.dumps(manifest, indent=2))
    print("Split counts:", {key: len(rows) for key, rows in splits.items()}, flush=True)
    print("Small test sets give unreliable accuracy. Keep photos from the same "
          "capture session in one split when collecting a larger dataset.", flush=True)
    report = {}
    for kind in ("cnn", "linear"):
        report[kind] = train_model(kind, splits, args.classes, args)
    best_kind = min(report, key=lambda kind: (
        -report[kind]["validation"]["accuracy"], report[kind]["validation"]["loss"]
    ))
    shutil.copyfile(args.output / f"{best_kind}.pt", args.output / "best.pt")
    report["selected_model"] = best_kind
    (args.output / "metrics.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
