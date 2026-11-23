"""Color COLMAP sparse points using the blue cup and robot car masks."""

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pycolmap
from PIL import Image


LABELS = {
    "blue cup": "blue cup",
    "robot car": "robot car",
    "small wheeled robot": "robot car",
}
COLORS = {"blue cup": (255, 115, 20), "robot car": (0, 210, 245)}


def choose_masks(instances):
    """Keep one mask per class in these scenes, which contain one cup and one car."""
    masks = {}
    for instance in instances:
        label = LABELS.get(instance["label"].strip().lower())
        if label is None:
            continue
        previous = masks.get(label)
        if previous is None or instance["detection_score"] > previous["detection_score"]:
            masks[label] = instance
    return masks


def resolve_labels(seen, votes, min_views):
    candidates = {}
    for label, class_votes in votes.items():
        for point_id, count in class_votes.items():
            if count >= min_views and 2 * count > seen[point_id]:
                candidates.setdefault(point_id, []).append(label)
    return {point_id: labels[0] for point_id, labels in candidates.items() if len(labels) == 1}


def label_points(reconstruction, mask_root, min_views):
    seen = Counter()
    votes = {label: Counter() for label in COLORS}
    matched_views = 0

    for view in sorted(reconstruction.images.values(), key=lambda image: image.name):
        mask_dir = mask_root / view.name
        manifest_path = mask_dir / "results.json"
        if not manifest_path.is_file():
            print(f"Skipping {view.name}: no mask manifest")
            continue

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        camera = reconstruction.cameras[view.camera_id]
        width, height = camera.width, camera.height
        manifest_size = (manifest["width"], manifest["height"])
        if manifest["image"] != Path(view.name).name or manifest_size != (width, height):
            raise ValueError(f"Mask manifest does not match the COLMAP image: {manifest_path}")

        observations = {}
        for point in view.points2D:
            x, y = point.xy
            if point.has_point3D() and 0 <= x < width and 0 <= y < height:
                observations[point.point3D_id] = (int(x), int(y))
        seen.update(observations.keys())
        matched_views += 1

        for label, instance in choose_masks(manifest["instances"]).items():
            mask_path = mask_dir / instance["mask"]
            with Image.open(mask_path) as image:
                mask = np.asarray(image.convert("L")) > 0
            if mask.shape != (height, width):
                raise ValueError(f"Mask dimensions do not match the COLMAP image: {mask_path}")
            hits = [point_id for point_id, (x, y) in observations.items() if mask[y, x]]
            votes[label].update(hits)
            print(f"{view.name}: {label}, {instance['mask']}, {len(hits)} observed points")

    if matched_views < min_views:
        raise ValueError(f"Found {matched_views} matching views; need at least {min_views}")
    return resolve_labels(seen, votes, min_views)


def write_ply(path, points, labels):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="ascii", newline="\n") as output:
        output.write(
            f"ply\nformat ascii 1.0\nelement vertex {len(points)}\n"
            "property float x\nproperty float y\nproperty float z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
        )
        for point_id, point in sorted(points.items()):
            xyz = " ".join(f"{value:.9g}" for value in point.xyz)
            color = COLORS.get(labels.get(point_id))
            if color is None:
                color = tuple(int(channel * 0.55) for channel in point.color)
            rgb = " ".join(str(channel) for channel in color)
            output.write(f"{xyz} {rgb}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True, help="COLMAP sparse model directory")
    parser.add_argument("--mask-root", type=Path, required=True, help="Per-image output from segment_tabletop.py")
    parser.add_argument("--output", type=Path, required=True, help="Path to a new colored PLY file")
    parser.add_argument("--min-views", type=int, default=2, help="Minimum positive views per point")
    args = parser.parse_args()

    if args.min_views < 1:
        parser.error("--min-views must be at least 1")
    if not args.model_dir.is_dir() or not args.mask_root.is_dir():
        parser.error("--model-dir and --mask-root must be existing directories")
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}")

    reconstruction = pycolmap.Reconstruction(str(args.model_dir))
    labels = label_points(reconstruction, args.mask_root, args.min_views)
    write_ply(args.output, reconstruction.points3D, labels)
    totals = Counter(labels.values())
    print(f"Scene points: {len(reconstruction.points3D)}")
    for label in COLORS:
        print(f"{label}: {totals[label]}")
    print(f"Saved {args.output.resolve()}")


if __name__ == "__main__":
    main()
