"""Train a candidate head detector from reviewed exports and compare it with the current one.

One command (decision 0002): merge exported YOLO packages by whole video, keep a
fixed benchmark list of videos as the test set (never trained on), fine-tune,
evaluate the current and the candidate detector on that same test set, and write
a version record. It never replaces backend/app/model_assets/head-detector.pt;
promoting a candidate is a separate, deliberate release step.

The benchmark file lists one source video SHA-256 per line (# starts a comment);
the SHA-256 values are in each package's manifest.json (sources[].sourceSha256).

Example:
  python3 scripts/detector_train_eval.py 训练数据集-第一批.zip 训练数据集-第二批.zip \
      --benchmark work/detector-benchmark.txt --output work/detector-runs/2026-11-01 --device 0

Use --dry-run to check the split and counts without training.
"""

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "backend" / "app" / "model_assets" / "head-detector.pt"
# A candidate must not lose recall and must gain box quality (mAP50-95) by at least this much.
MIN_GAIN = 0.005


def merge_module():
    spec = importlib.util.spec_from_file_location("merge_training_exports", Path(__file__).with_name("merge_training_exports.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_benchmark(path):
    shas = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if not line:
            continue
        if len(line) != 64 or any(c not in "0123456789abcdef" for c in line):
            raise SystemExit(f"基准测试视频清单格式无效（应为 64 位 SHA-256）：{raw}")
        shas.append(line)
    if not shas:
        raise SystemExit("基准测试视频清单为空")
    return sorted(set(shas))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def edited_counts(videos, chosen):
    counts = {s: {"boxes": 0, "reviewEdited": 0} for s in ("train", "val", "test")}
    for v, split in zip(videos, chosen):
        for sample in v["samples"]:
            for obj in sample.get("objects", []):
                counts[split]["boxes"] += 1
                counts[split]["reviewEdited"] += obj.get("reviewEdited") is True
    return counts


def train(init, data, output, epochs, imgsz, device):
    from ultralytics import YOLO
    YOLO(str(init)).train(data=str(data), epochs=epochs, imgsz=imgsz, device=device,
                          project=str(output), name="train", exist_ok=True, verbose=False)
    best = output / "train" / "weights" / "best.pt"
    if not best.is_file():
        raise SystemExit(f"训练结束但没有生成 {best}")
    return best


def evaluate(weights, data, output, name, imgsz, device):
    from ultralytics import YOLO
    result = YOLO(str(weights)).val(data=str(data), split="test", imgsz=imgsz, device=device,
                                     project=str(output), name=name, exist_ok=True, verbose=False)
    box = result.box
    return {"mAP50": float(box.map50), "mAP50_95": float(box.map), "precision": float(box.mp), "recall": float(box.mr)}


def verdict(baseline, candidate):
    better = candidate["mAP50_95"] >= baseline["mAP50_95"] + MIN_GAIN and candidate["recall"] >= baseline["recall"] - MIN_GAIN
    return "better" if better else "not_better"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", help="导出的 YOLO 训练包（ZIP 或解压目录）")
    parser.add_argument("--benchmark", required=True, help="固定测试集：每行一个原视频 SHA-256")
    parser.add_argument("--output", required=True, help="本次运行目录（必须不存在或为空），建议放在 work/ 下")
    parser.add_argument("--baseline", default=str(BUNDLED), help="当前检测器权重，默认仓库内 head-detector.pt")
    parser.add_argument("--init", help="微调起点，默认与 --baseline 相同")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--val", type=float, default=0.15, help="非基准视频中验证集的比例")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--dry-run", action="store_true", help="只合并并统计，不训练")
    args = parser.parse_args(argv)
    if not 0 < args.val < 1:
        parser.error("--val 需在 0 和 1 之间")
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        parser.error(f"输出目录不为空：{output}")
    output.mkdir(parents=True, exist_ok=True)
    benchmark = read_benchmark(args.benchmark)
    merge = merge_module()
    data_dir = output / "dataset"
    with tempfile.TemporaryDirectory() as scratch:
        videos = merge.collect(list(merge.find_packages(args.inputs, scratch)))
        present = {v["sha"] for v in videos}
        missing = [sha for sha in benchmark if sha not in present]
        if len(missing) == len(benchmark):
            raise SystemExit("训练包中没有任何基准测试视频，无法评估")
        if missing:
            print(f"警告：{len(missing)} 个基准视频不在本次训练包中，本次测试集不完整", file=sys.stderr)
        # Benchmark videos are pinned to test; every other video goes to train/val only.
        chosen = merge.assign(videos, 1 - args.val, 0, {sha: "test" for sha in benchmark})
        data_dir.mkdir()
        counts = merge.merge(videos, chosen, data_dir, with_videos=False)
        edited = edited_counts(videos, chosen)
    record = {
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark": {"videos": benchmark, "missing": missing},
        "exports": sorted({v["exportId"] for v in videos}),
        "splits": {s: {**counts[s], **edited[s]} for s in counts},
        "params": {"epochs": args.epochs, "imgsz": args.imgsz, "device": args.device, "val": args.val},
        "baseline": {"weights": str(args.baseline), "sha256": sha256(args.baseline)},
        "dryRun": args.dry_run,
    }
    data = data_dir / "data.yaml"
    if not args.dry_run:
        init = Path(args.init or args.baseline)
        record["init"] = {"weights": str(init), "sha256": sha256(init)}
        best = train(init, data, output, args.epochs, args.imgsz, args.device)
        record["candidate"] = {"weights": str(best), "sha256": sha256(best)}
        record["baseline"]["metrics"] = evaluate(args.baseline, data, output, "eval-baseline", args.imgsz, args.device)
        record["candidate"]["metrics"] = evaluate(best, data, output, "eval-candidate", args.imgsz, args.device)
        record["verdict"] = verdict(record["baseline"]["metrics"], record["candidate"]["metrics"])
    (output / "model-record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    for split, c in record["splits"].items():
        print(f"{split}: {c['videos']} 个视频，{c['frames']} 帧，{c['boxes']} 个框（审查员改过 {c['reviewEdited']} 个）")
    if not args.dry_run:
        b, c = record["baseline"]["metrics"], record["candidate"]["metrics"]
        print(f"当前模型  mAP50-95 {b['mAP50_95']:.3f}  召回 {b['recall']:.3f}")
        print(f"候选模型  mAP50-95 {c['mAP50_95']:.3f}  召回 {c['recall']:.3f}")
        print("结论：候选模型更好，可考虑发布" if record["verdict"] == "better" else "结论：候选模型没有明显更好，不建议发布")
    print(f"记录已写入 {output / 'model-record.json'}")
    return record


if __name__ == "__main__":
    main()
