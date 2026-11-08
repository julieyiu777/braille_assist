"""Product scores with explicit rejection of non-target inputs."""
import cv2
import numpy as np
import torch
from PIL import Image, ImageOps
from torch import nn
from torchvision import transforms
from torchvision.models import resnet18

TARGETS = ["chips", "soda", "pens"]


def activation_bounds(activation, shape):
    """Estimate product bounds from the strongest connected activation region."""
    height, width = shape[:2]
    heatmap = cv2.resize(np.maximum(activation, 0), (width, height))
    peak = float(heatmap.max())
    if peak <= 0:
        return None
    mask = (heatmap >= peak * .35).astype(np.uint8)
    count, regions, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return None
    peak_y, peak_x = np.unravel_index(heatmap.argmax(), heatmap.shape)
    x, y, w, h, _ = stats[regions[peak_y, peak_x]]
    padding = max(2, int(min(width, height) * .03))
    return (max(0, x-padding), max(0, y-padding),
            min(width, x+w+padding), min(height, y+h+padding))


def make_model(kind, pretrained=False):
    if kind == "cnn":
        from torchvision.models import ResNet18_Weights
        model = resnet18(weights=ResNet18_Weights.DEFAULT if pretrained else None)
        model.fc = nn.Linear(model.fc.in_features, len(TARGETS))
        return model
    if kind == "linear":
        return nn.Sequential(nn.Flatten(), nn.Linear(3*32*32, len(TARGETS)))
    raise ValueError(f"Unsupported model: {kind}")


def preprocess(kind):
    size = 224 if kind == "cnn" else 32
    mean, std = ((.485,.456,.406), (.229,.224,.225)) if kind == "cnn" else ((.5,)*3, (.5,)*3)
    return transforms.Compose([
        # Simulate the default camera placement area's 1.6:1 aspect ratio
        # before the square network input. Standalone catalog images must
        # have the same product scale as images presented in that rectangle.
        transforms.Lambda(lambda image: ImageOps.pad(image, (360,224), color=(128,128,128))),
        transforms.Lambda(lambda image: ImageOps.pad(image, (size,size), color=(128,128,128))),
        transforms.ToTensor(), transforms.Normalize(mean, std),
    ])


def rejection_loss(logits, labels, negative_weight=2.0):
    """Negatives (label len(TARGETS)) have zero targets for every product logit."""
    targets = torch.zeros_like(logits)
    positive = labels < len(TARGETS)
    targets[positive, labels[positive]] = 1
    losses = nn.functional.binary_cross_entropy_with_logits(logits, targets, reduction="none").mean(1)
    # Average positives and negatives separately to avoid class-count dominance.
    parts = []
    if positive.any():
        parts.append(losses[positive].mean())
    if (~positive).any():
        parts.append(negative_weight * losses[~positive].mean())
    return sum(parts)


def accepted_label(scores, threshold=.85, margin=.25):
    index = int(np.argmax(scores))
    other_scores = np.delete(scores, index)
    acceptance = float(threshold) if np.ndim(threshold) == 0 else threshold[index]
    if scores[index] < acceptance or scores[index] - other_scores.max() < margin:
        return None
    return TARGETS[index]


class ROIClassifier:
    def __init__(self, path, threshold=None, margin=None):
        saved = torch.load(path, map_location="cpu", weights_only=True)
        if saved.get("task") != "roi_product_rejection":
            raise ValueError("Use a checkpoint trained by train_roi.py")
        if saved.get("classes") != TARGETS:
            raise ValueError("Checkpoint classes do not match the current targets; retrain with train_roi.py")
        self.model = make_model(saved["kind"])
        self.model.load_state_dict(saved["state_dict"])
        self.model.eval()
        self.transform = preprocess(saved["kind"])
        self.threshold = saved.get("class_thresholds", saved["threshold"]) if threshold is None else threshold
        self.margin = saved["margin"] if margin is None else margin
        self.pen_localizer = None
        self.pen_verification_threshold = saved.get('pen_verification_threshold',.95)
        self.pen_global_fallback_threshold = saved.get('pen_global_fallback_threshold',1.01)
        if saved.get('pen_localizer'):
            from pen_localizer import PenLocalizer
            self.pen_localizer=PenLocalizer(saved['pen_localizer'],saved.get('pen_detector_threshold',.1),
                                           saved.get('pen_localizer_image_size',384))

    def predict(self, bgr):
        image = Image.fromarray(bgr[:,:,::-1].copy())
        with torch.inference_mode():
            scores = self.model(self.transform(image).unsqueeze(0)).sigmoid()[0].numpy()
        return accepted_label(scores, self.threshold, self.margin), scores

    def predict_localized(self, bgr):
        """Return verified pen bounds or approximate CNN activation bounds."""
        if self.pen_localizer is not None:
            from pen_localizer import verify_proposals
            verified=verify_proposals(self,bgr,self.pen_localizer.proposals(bgr),
                self.pen_localizer.confidence,self.pen_verification_threshold)
            if verified is not None:
                return verified
        if not hasattr(self.model, "layer4"):
            raise ValueError("Object boxes require a CNN checkpoint from train_roi.py")
        features = []
        handle = self.model.layer4.register_forward_hook(
            lambda module, inputs, output: features.append(output.detach()))
        try:
            label, scores = self.predict(bgr)
        finally:
            handle.remove()
        if label is None or (label=='pens' and self.pen_localizer is not None and
                             scores[TARGETS.index('pens')]<self.pen_global_fallback_threshold):
            return None, scores, None
        index = TARGETS.index(label)
        with torch.inference_mode():
            activation = (features[0][0] * self.model.fc.weight[index, :, None, None]).sum(0)
        # preprocess pads to 360:224, then pads that image to a square. Undo
        # both letterboxes before mapping the heatmap to camera coordinates.
        heatmap = cv2.resize(activation.detach().numpy(), (360, 360))
        heatmap = heatmap[68:292, :]
        height, width = bgr.shape[:2]
        scale = min(360 / width, 224 / height)
        fitted_w, fitted_h = round(width * scale), round(height * scale)
        left, top = (360-fitted_w)//2, (224-fitted_h)//2
        heatmap = heatmap[top:top+fitted_h, left:left+fitted_w]
        return label, scores, activation_bounds(heatmap, bgr.shape)
