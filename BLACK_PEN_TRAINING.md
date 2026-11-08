The live camera now defaults to `runs/black_pen_active/best.pt`. This run adds
95 reviewed black/dark-barrel and black-grip pen photographs from Open Images,
plus 65 reviewed non-pen examples. The pen class grew from 33 to 128 photos.
The complete classifier dataset contains 273 images: 16 chips, 10 soda,
128 pens, and 119 negatives, split 186 train / 44 validation / 43 test.

The previous camera archive showed that the presented pen occupied a relatively
small region of a cluttered room. Training only on isolated catalog photos did
not address that problem. The new data includes writing, hand-held pens, varied
backgrounds, orientations, and annotated full-scene views. A trained pen
localizer can propose a region for the classifier to inspect. Proposed regions
must pass the classifier's pen acceptance check before display. A separate,
more stringent global pen fallback preserves strong whole-frame matches.

Measured on the same expanded web test partition:

| Metric | Previous pipeline | New pipeline |
| --- | --- | --- |
| Pen context images recognized | 16/20 | 18/20 |
| Non-pen images falsely called pens | 3/23 | 1/23 |
| All context images correct | 36/43 | 40/43 |
| Annotated full-scene pen views recognized | 9/14 | 11/14 |

Full-scene and context views share their source photographs and are not
independent extra examples. In a separate diagnostic using 12 visually reviewed
frames from one saved camera session, pen recognition increased from 0/3 to 1/3.
Two pen frames were still called soda. This remains an important live-camera
limitation. These correlated frames are not an independent accuracy benchmark,
were labeled by assistant visual review rather than the user, and were excluded
from training, threshold calibration, and model selection. No new live camera
recording was started during this work.

Data sources and review:

- [Open Images official download/annotation documentation](https://storage.googleapis.com/openimages/web/download_v7.html).
- The verified `Pen` class is `/m/0k1tl`. The collector downloaded its 36
  validation and 94 test originals, plus a bounded 160-image training subset.
  These upstream partition names are provenance; this project's custom split
  is not an official Open Images benchmark.
- Of 290 downloaded pen candidates, 95 new images passed black/dark-pen visual
  review and duplicate screening. Colored-only images, markers/highlighters,
  uncertain objects, and extreme tip-only macros were excluded.
- Additional negatives include remotes, phones, knives, screwdrivers, spoons,
  scissors, sunglasses, toothbrushes, and non-pen background examples. Some
  source `Lipstick` boxes show faces rather than a lipstick tube; they were
  reviewed as non-pen backgrounds, not described as tube look-alikes.
- Original landing URLs, authors, source image URLs, annotation boxes, and
  individual image-license metadata are retained in
  `data/openimage_pens/reviewed_sources.json` and
  `data/openimage_pens/negative_reviewed_sources.json`.
- Both context crops and original images are retained. They count as one source
  photograph. New images are grouped by source photographer; these 149 new
  source groups do not cross partitions. Legacy partitions remain unchanged.
- Duplicate/near-duplicate screening includes earlier data. All 273 selected
  context images have distinct decoded pixel hashes. This does not prove that
  every image is statistically independent; small web datasets remain limited.

Training and inference:

1. Fine-tune the prior classifier with the expanded pen set and annotated scene
   views (18 epochs, learning rate 0.00005; selected epoch 3).
2. Add reviewed negatives and fine-tune for 14 epochs at 0.00003 with negative
   loss weight 3 (selected epoch 9). This stage preserves full annotated scene
   bounds instead of translating a small edge object out of view. The original
   first-stage run allowed affine augmentation of these original views; current
   training code uses the corrected preservation behavior.
3. Train a one-class YOLOv8n pen localizer using original annotated pen photos
   and reviewed negative images. Detection splits contain 148 training, 33
   validation, and 33 test images, preserving the same source boundaries.
   The run was capped at 25 epochs and stopped after 10 with patience 7.
   Detector-only validation mAP50 was about 0.32; this localizer is imperfect.
4. Compare both classifiers, ordinary/global versus region-verified inference,
   detector input sizes 384/640, and confidence settings using web validation
   context and annotated full-scene views only. Select balanced accuracy minus
   half the false-pen rate. Test and camera diagnostics do not choose settings.
5. The selected pipeline uses the second classifier, detector input 640,
   detector confidence 0.35, classifier pen acceptance at least 0.95 with its
   0.35 score margin, and a global pen fallback of 0.99. Chips and soda retain
   their classifier pathway. A verified pen proposal can override a whole-frame
   non-pen prediction; this remains a one-presented-item workflow.

Classifier-only metrics in the intermediate training directories are different
from final pipeline metrics. Use `runs/black_pen_active/comparison.json` for
the full inference comparison and `selection.json` for validation selection.
The selected checkpoint references the localizer under
`runs/black_pen_localizer/weights/best.pt`; keep that file with the application.
Older checkpoints remain available for rollback.

Reproduction commands (initial downloads require internet access):

```bash
.venv/bin/python collect_openimage_pens.py
.venv/bin/python build_black_pen_split.py
.venv/bin/python collect_pen_negatives.py
.venv/bin/python build_black_pen_negatives.py
.venv/bin/python train_roi.py --split data/openimage_pens/split.json --output runs/black_pen_expanded --epochs 18 --kinds cnn --init runs/rollerball_active/best.pt --learning-rate 0.00005
.venv/bin/python train_roi.py --split data/openimage_pens/split_with_negatives.json --output runs/black_pen_balanced --epochs 14 --kinds cnn --init runs/black_pen_expanded/best.pt --learning-rate 0.00003 --negative-weight 3
.venv/bin/python train_pen_localizer.py
.venv/bin/python calibrate_black_pen_pipeline.py
.venv/bin/python evaluate_black_pens.py
.venv/bin/python -m unittest test_detection test_black_pen_data test_tiger_data
.venv/bin/python detect.py
```

`evaluate_black_pens.py` uses an optional local camera diagnostic manifest whose
image paths must still exist. Source photographs stay in their original
partitions, and camera frames are never silently added to training. New camera
sessions with user-verified labels are the next useful independent evaluation.
