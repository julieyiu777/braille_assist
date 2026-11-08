"""Train CNN/linear product models with people and scenes in the rejection loss."""
import argparse
import json
import random
import shutil
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageOps
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import transforms

from roi_model import TARGETS, accepted_label, make_model, preprocess, rejection_loss
from train_products import split_images


def isolate_catalog_pen(foreground):
    """Keep the main connected pen, removing separate boxes and logo artwork."""
    alpha = np.asarray(foreground.getchannel("A"))
    mask = cv2.morphologyEx((alpha > 0).astype(np.uint8), cv2.MORPH_CLOSE,
                            np.ones((3, 3), dtype=np.uint8))
    count, components, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return foreground
    main = 1+int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    keep = cv2.dilate((components == main).astype(np.uint8), np.ones((3, 3), dtype=np.uint8))
    result = foreground.copy()
    result.putalpha(Image.fromarray(np.where(keep, alpha, 0).astype(np.uint8)))
    return result


class ROIData(Dataset):
    def __init__(self, rows, kind, training=False, backgrounds=(), training_views=None):
        self.rows, self.training, self.backgrounds = rows, training, backgrounds
        self.transform = preprocess(kind)
        self.jitter = transforms.ColorJitter(.3,.3,.2,.03)
        self.training_views = training_views or {}

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        path, label = self.rows[index]
        with Image.open(path) as source:
            image = source.convert("RGB")
        scene_view=False
        if self.training and path in self.training_views and random.random()<.5:
            # Use the original annotated scene as well as its context crop.
            # Both views belong to one source image, exclusively in training.
            with Image.open(self.training_views[path]['original_path']) as source:
                image=source.convert('RGB')
            scene_view=True
        if self.training:
            rgb = np.asarray(image)
            white = (rgb.min(2) > 232).astype(np.uint8)
            border_white = np.concatenate([white[0], white[-1], white[:, 0], white[:, -1]]).mean()
            if label < len(TARGETS) and not scene_view and border_white > .9 and random.random() < .75:
                # Remove only border-connected white catalog background; retain
                # white labels printed inside the product silhouette.
                _, components = cv2.connectedComponents(white)
                border = np.unique(np.concatenate([components[0],components[-1],components[:,0],components[:,-1]]))
                border = border[border != 0]
                alpha = np.where(np.isin(components,border),0,255).astype(np.uint8)
                foreground = image.convert("RGBA")
                foreground.putalpha(Image.fromarray(alpha))
                if TARGETS[label] == "pens":
                    foreground = isolate_catalog_pen(foreground)
                bounds = foreground.getbbox()
                if bounds:
                    foreground = foreground.crop(bounds)
                rotation = 180 if TARGETS[label] == "pens" else 18
                foreground = foreground.rotate(random.uniform(-rotation,rotation), expand=True)
                foreground.thumbnail((int(random.uniform(.35,.95)*360), int(random.uniform(.35,.95)*224)))
                if self.backgrounds and random.random() < .75:
                    with Image.open(random.choice(self.backgrounds)) as source:
                        canvas = ImageOps.fit(source.convert("RGB"),(360,224)).convert("RGBA")
                else:
                    canvas = Image.new("RGBA",(360,224),tuple(random.randint(30,230) for _ in range(3))+(255,))
                canvas.alpha_composite(foreground,(random.randint(0,360-foreground.width), random.randint(0,224-foreground.height)))
                image = canvas.convert("RGB")
            elif label == len(TARGETS) and random.random() < .15:
                # Empty placement ledges should suppress both target scores.
                image = Image.new("RGB",(224,224),tuple(random.randint(0,255) for _ in range(3)))
            elif label < len(TARGETS) and not scene_view:
                # Preserve real scenes rather than cutting white objects out of
                # desks and notebooks as though they were catalog backgrounds.
                image = transforms.RandomAffine(25, translate=(.08, .08),
                                                scale=(.8, 1.1), fill=(128,128,128))(image)
            image = self.jitter(image)
        return self.transform(image), label


def scores_for(model, loader):
    model.eval()
    scores, labels = [], []
    with torch.inference_mode():
        for images, target in loader:
            scores.extend(model(images).sigmoid().tolist())
            labels.extend(target.tolist())
    return np.array(scores), np.array(labels)


def metrics(scores, labels, threshold, margin):
    negative_index = len(TARGETS)
    confusion = np.zeros((negative_index + 1, negative_index + 1),dtype=int)
    for values,label in zip(scores, labels):
        name = accepted_label(values, threshold, margin)
        confusion[label, negative_index if name is None else TARGETS.index(name)] += 1
    recall = confusion.diagonal()/confusion.sum(1).clip(1)
    pen_index = TARGETS.index("pens")
    nonpens = confusion.sum()-confusion[pen_index].sum()
    pen_false_accepts = confusion[:, pen_index].sum()-confusion[pen_index, pen_index]
    return {"balanced_accuracy":float(recall.mean()),
            "accuracy":float(confusion.trace()/confusion.sum()),
            "negative_false_accept_rate":float(1-recall[negative_index]),
            "pen_false_accept_rate":float(pen_false_accepts/max(1, nonpens)),
            "per_class_recall":recall.tolist(), "confusion_matrix":confusion.tolist()}


def calibrate(scores, labels):
    options = []
    for threshold in (.65, .75, .85, .9, .95):
        for margin in (.15, .25, .35):
            for pen_threshold in (.85, .9, .95, .98, .995):
                thresholds = [threshold]*len(TARGETS)
                thresholds[TARGETS.index("pens")] = pen_threshold
                report = metrics(scores, labels, thresholds, margin)
                # Among equally good validation settings, use the least strict
                # pen cutoff that rejects false matches, to retain true pens.
                # Balance recognition and false matches rather than selecting
                # a model that rejects nearly every pen to reach zero errors.
                objective = report["balanced_accuracy"]-.5*report["pen_false_accept_rate"]
                key = (objective, -report["pen_false_accept_rate"], report["balanced_accuracy"],
                       -report["negative_false_accept_rate"], threshold, margin, -pen_threshold)
                options.append((key, threshold, thresholds, margin, report))
    return max(options, key=lambda option: option[0])


def fit(kind, splits, args):
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    model = make_model(kind, pretrained=kind=="cnn")
    if args.init:
        checkpoint = torch.load(args.init, map_location="cpu", weights_only=True)
        if checkpoint.get("classes") != TARGETS or checkpoint.get("kind") != kind:
            raise ValueError("Initialization checkpoint must have matching classes and model kind")
        model.load_state_dict(checkpoint["state_dict"])
    if kind == "cnn":
        for parameter in model.parameters():
            parameter.requires_grad = False
        for module in (model.layer4,model.fc):
            for parameter in module.parameters():
                parameter.requires_grad = True
    learning_rate = args.learning_rate if args.learning_rate is not None else (.0001 if kind=="cnn" else .001)
    optimizer = torch.optim.AdamW(filter(lambda p:p.requires_grad,model.parameters()),lr=learning_rate)
    backgrounds = [path for path,label in splits["train"] if label == len(TARGETS)]
    counts = np.bincount([label for _, label in splits["train"]], minlength=len(TARGETS)+1)
    weights = [1/counts[label] for _, label in splits["train"]]
    sampler = WeightedRandomSampler(weights, num_samples=len(weights)*2, replacement=True)
    loaders = {split:DataLoader(ROIData(rows,kind,split=="train",backgrounds,args.training_views),batch_size=8,
                               sampler=sampler if split=="train" else None)
               for split,rows in splits.items()}
    best_key, best_epoch = None, None
    path = args.output/f"{kind}.pt"
    for epoch in range(1,args.epochs+1):
        model.train()
        if kind == "cnn":
            for module in model.modules():
                if isinstance(module,torch.nn.BatchNorm2d):
                    module.eval()
        for images,labels in loaders["train"]:
            optimizer.zero_grad()
            loss = rejection_loss(model(images), labels, args.negative_weight)
            loss.backward()
            optimizer.step()
        scores,labels = scores_for(model,loaders["val"])
        key,threshold,thresholds,margin,report = calibrate(scores, labels)
        print(f"{kind} epoch {epoch}: validation balanced accuracy={report['balanced_accuracy']:.3f}, negative false accepts={report['negative_false_accept_rate']:.3f}",flush=True)
        if best_key is None or key > best_key:
            best_key,best_epoch = key,epoch
            torch.save({"task":"roi_product_rejection","kind":kind,"classes":TARGETS,
                        "state_dict":model.state_dict(),"threshold":threshold,"margin":margin,
                        "class_thresholds":thresholds,
                        "learning_rate":learning_rate,"initial_checkpoint":str(args.init) if args.init else None,
                        "epoch":epoch,"negative_weight":args.negative_weight},path)
    saved = torch.load(path,weights_only=True)
    model.load_state_dict(saved["state_dict"])
    report = {"best_epoch":best_epoch,"threshold":saved["threshold"],"margin":saved["margin"],
              "class_thresholds":saved["class_thresholds"]}
    for split in ("val","test"):
        scores,labels = scores_for(model,loaders[split])
        report[split] = metrics(scores,labels,saved["class_thresholds"],saved["margin"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data",type=Path,default=Path("data/roi_targets"))
    parser.add_argument("--output",type=Path,default=Path("runs/fullscreen_targets"))
    parser.add_argument("--epochs",type=int,default=25)
    parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--negative-weight",type=float,default=2)
    parser.add_argument("--split", type=Path, help="Reuse a reviewed train/val/test split JSON.")
    parser.add_argument("--kinds", nargs="+", choices=("cnn", "linear"), default=("cnn", "linear"))
    parser.add_argument("--init", type=Path, help="Fine-tune a checkpoint with the same classes and kind.")
    parser.add_argument("--learning-rate", type=float, help="Override the model's training learning rate.")
    args = parser.parse_args()
    if args.epochs < 1 or args.negative_weight <= 0 or (args.learning_rate is not None and args.learning_rate <= 0):
        parser.error("epochs, negative-weight, and learning-rate must be positive")
    torch.set_num_threads(4)
    classes = [*TARGETS,"negative"]
    if args.split:
        reviewed = json.loads(args.split.read_text())
        if reviewed.get("classes") != classes:
            parser.error("Reviewed split class order does not match the model targets")
        splits = reviewed["splits"]
        args.training_views=reviewed.get('training_views',{})
    else:
        splits = split_images(args.data,classes,args.seed)
        args.training_views={}
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/"split.json").write_text(json.dumps({"seed":args.seed,"classes":classes,"splits":splits},indent=2))
    print("Split counts:",{key:len(rows) for key,rows in splits.items()},flush=True)
    report = {kind:fit(kind,splits,args) for kind in args.kinds}
    best = max(report,key=lambda kind:(report[kind]["val"]["balanced_accuracy"]-.5*report[kind]["val"]["pen_false_accept_rate"],
                                     -report[kind]["val"]["pen_false_accept_rate"],
                                     -report[kind]["val"]["negative_false_accept_rate"]))
    shutil.copyfile(args.output/f"{best}.pt",args.output/"best.pt")
    report["selected_model"] = best
    report["metric_class_order"] = classes
    (args.output/"metrics.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
