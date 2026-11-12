# Bundled sperm-head detector

`head-detector.pt` is the YOLO26s checkpoint used by this annotation tool. It is a normal Git file (about 19.4 MiB), not a Git LFS pointer. Cloning or pulling this repository retrieves it without a separate model download.

The default backend resolver uses this file. `HEAD_DETECTOR_MODEL` remains an explicit custom-model override. Install `backend/requirements.txt` normally; it includes the pinned Ultralytics runtime. No inference-time downloads are needed. `HEAD_DETECTOR_DEVICE` defaults to `cuda:0`; GPU inference fails visibly if CUDA is unavailable. An explicit device override is available for other deployments.

The checksum, single class `sperm`, training provenance and runtime version are pinned in `head-detector.json`. Since 2026-11 this is `yolo26s-sperm264-head-v6`: the previous SVIA-only YOLO26s model fine-tuned for 40 epochs at input size 1024 on this tool's reviewed hospital annotations (sperm264 v6 labels, train+val). It was trained with Ultralytics 8.4.202 and loads with the pinned 8.4.183 runtime (boxes match within one pixel). It contains model tensors and training metadata, without hospital images, videos or annotation files.

Inference must match training: grayscale frame copied to 3 channels, the whole frame resized so its long side is 1024 (`cv2.INTER_AREA`), `imgsz=1024`, `iou=0.7`, coordinates scaled back to source pixels (`head_click._predict_full`). Do not run it on native-resolution tiles: heads would be 2–4× larger than in training. `HEAD_DETECTOR_CONF` (default 0.30) is the confident-head threshold; `HEAD_DETECTOR_LOW_CONF` (default 0.10) is used for clicks and for extending existing tracks.

The checkpoint records the Ultralytics AGPL-3.0 license; the complete upstream license is included in `LICENSE-AGPL-3.0.txt`. Framework source: https://github.com/ultralytics/ultralytics/tree/v8.4.183 . This notice does not grant additional rights over third-party code or datasets.

Training data: SVIA Subset-A, Chen Li et al., *SVIA dataset: A new dataset of microscopic videos and images for computer-aided sperm analysis*, 2022. Source: https://github.com/Demozsj/Detection-Sperm ; https://doi.org/10.6084/m9.figshare.15074253.v1 . The dataset authors welcome non-commercial research. The initial weights come from that SVIA model. This package is intended for non-commercial research annotation; it does not expand the dataset's terms or establish clinical/diagnostic fitness. Predictions remain editable proposals, not expert ground truth.

Retraining on hospital annotations: `scripts/detector_train_eval.py` fine-tunes from this checkpoint on reviewed YOLO exports, keeps a fixed benchmark list of videos as the test set, and compares the candidate with this model (`model-record.json`). It never replaces this file; publishing a new detector, updating `head-detector.json` and choosing how to distribute the weight are separate decisions (docs/decisions/0002, 0006).
