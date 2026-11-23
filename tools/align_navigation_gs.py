"""Register a Brush reconstruction to a navigation scene using shared cameras."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pycolmap
from PIL import Image
from scipy import ndimage

from cup_visual_hull import cup_visual_hull, load_cup_views
from label_sparse_model import COLORS
from plan_car_route import triangulate


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fit_similarity(source, destination):
    source_center = source.mean(axis=0)
    destination_center = destination.mean(axis=0)
    a, b = source - source_center, destination - destination_center
    if np.linalg.matrix_rank(a) < 2 or np.linalg.matrix_rank(b) < 2:
        raise ValueError("Need noncollinear shared camera centers")
    u, singular, vt = np.linalg.svd(b.T @ a)
    signs = np.array([1, 1, np.linalg.det(u @ vt)])
    rotation = (u * signs) @ vt
    scale = (singular @ signs) / np.sum(a * a)
    translation = destination_center - scale * rotation @ source_center
    return scale, rotation, translation


def build_manifest(scene, reference_model, reference_images, job, mask_root=None, figurine_root=None,
                   figurine_views=(), objects=None):
    route = json.loads(scene.read_text(encoding="utf-8"))
    inputs = json.loads((job / "inputs.json").read_text(encoding="utf-8"))
    result = json.loads((job / "result.json").read_text(encoding="utf-8"))
    if not result.get("gs_ready") or not (job / "gaussians.ply").is_file():
        raise ValueError("The reconstruction job must contain a completed Brush model")

    reference = pycolmap.Reconstruction(str(reference_model))
    trained = pycolmap.Reconstruction(str(job / result["sparse_model"]))
    reference_views = {view.name: view for view in reference.images.values()}
    pairs = []
    for view in trained.images.values():
        name = Path(inputs["images"][view.name]).name
        if name not in reference_views:
            continue
        if digest(reference_images / name) != digest(job / "images" / view.name):
            raise ValueError(f"Registered photograph differs: {name}")
        pairs.append((reference_views[name], view))
    if len(pairs) < 3:
        raise ValueError("Need at least three shared registered photos")
    old, new = (np.array([pair[i].projection_center() for pair in pairs]) for i in (0, 1))
    scale, rotation, translation = fit_similarity(old, new)

    def transform(point):
        return scale * (rotation @ point) + translation

    fitted = np.array([transform(point) for point in old])
    error = np.linalg.norm(fitted - new, axis=1)
    baseline = np.linalg.norm(new - new.mean(axis=0), axis=1).max()
    angles = [
        np.degrees(np.arccos(np.clip((rotation @ a.viewing_direction()) @ b.viewing_direction(), -1, 1)))
        for a, b in pairs
    ]
    if error.max() > .05 * baseline or max(angles) > 5:
        raise ValueError("The two reconstructions do not agree on shared camera poses")

    def viewer_point(point):
        # SuperSplat rotates PLY data 180 degrees about Z.
        return (transform(point) * [-1, -1, 1]).tolist()

    cloud = scene.parent / route["point_cloud"]
    rows = cloud.read_text(encoding="ascii").split("end_header", 1)[1].splitlines()
    points = np.loadtxt(rows, ndmin=2)
    cup = points[np.all(points[:, 3:] == COLORS["blue cup"], axis=1), :3]
    if len(cup) < 3:
        raise ValueError("The navigation cloud must contain labeled blue-cup points")
    manifest = {
        "scene_sha256": digest(scene),
        "gaussians": "gaussians.ply",
        "camera": result["gs_camera"],
        "geometry": {key: viewer_point(route["geometry"][key]) for key in ("start", "front", "target", "stop")},
        "cup_points": [viewer_point(point) for point in cup],
        "alignment": {
            "shared_cameras": len(pairs),
            "max_center_error_scene_units": float(error.max()),
            "max_direction_error_deg": float(max(angles)),
        },
    }
    if mask_root is not None:
        views = load_cup_views(trained, pairs, mask_root)
        seeds = np.array([transform(point) for point in cup])
        outline = cup_visual_hull(seeds, views)
        manifest["cup_outline"] = (outline * [-1, -1, 1]).tolist()
        manifest["alignment"]["cup_mask_views"] = len(views)
    if figurine_root is not None:
        target = build_figurine(route, reference, figurine_root, figurine_views)
        geometry = target["geometry"]
        target["gs_navigation"] = {
            "geometry": {key: viewer_point(geometry[key]) for key in ("start", "front", "target", "stop")},
            "cup_outline": [viewer_point(point) for point in target.pop("outline")],
        }
        manifest["targets"] = [target]
    if objects is not None:
        for item in json.loads(objects.read_text(encoding="utf-8")):
            observations = {
                name: {"image_size": record["image_size"], "center": record["center"]}
                for name, record in item["observations"].items()
                if name in reference_views
            }
            center, errors = triangulate(reference, observations, "center")
            if max(errors.values()) > 10:
                raise ValueError(f"Target marks disagree for {item['name']}")
            geometry = {key: route["geometry"][key] for key in ("start", "front", "up")}
            geometry.update(target=center.tolist())
            manifest.setdefault("targets", []).append({
                **{key: item[key] for key in ("id", "name", "keywords", "unavailable_reason")},
                "geometry": geometry,
                "gs_navigation": {"geometry": {key: viewer_point(geometry[key]) for key in ("target",)}},
            })
    return manifest


def build_figurine(route, reconstruction, root, names):
    observations = {}
    views = []
    for view in reconstruction.images.values():
        if view.name not in names:
            continue
        folder = root / view.name
        camera = reconstruction.cameras[view.camera_id]
        metadata = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        if metadata["image"] != view.name or (metadata["width"], metadata["height"]) != (camera.width, camera.height):
            raise ValueError(f"Figurine mask metadata does not match {view.name}")
        detection = max((item for item in metadata["instances"] if item["label"] == "toy figurine"),
                        key=lambda item: item["detection_score"])
        with Image.open(folder / detection["mask"]) as image:
            mask = np.asarray(image.convert("L")) > 0
        if mask.shape != (camera.height, camera.width) or not mask.any():
            raise ValueError(f"Invalid figurine mask in {view.name}")
        y, x = np.where(mask)
        observations[view.name] = {"image_size": [camera.width, camera.height],
                                   "center": [float(np.median(x)), float(np.median(y))]}
        pose = view.cam_from_world
        views.append((camera, pose() if callable(pose) else pose, ndimage.binary_dilation(mask, iterations=4)))
    center, errors = triangulate(reconstruction, observations, "center")
    if max(errors.values()) > 10:
        raise ValueError("Figurine masks do not agree on the target position")
    outline = cup_visual_hull(np.array([center - 1.5, center + 1.5]), views)
    start, front, up = (np.asarray(route["geometry"][key]) for key in ("start", "front", "up"))
    heading = front - start
    toward = center - start
    toward -= (toward @ up) * up
    direction = toward / np.linalg.norm(toward)
    angle = np.degrees(np.arctan2(up @ np.cross(heading, direction), heading @ direction))
    scale = route["cm_per_scene_unit"]
    near = np.quantile((outline - start) @ direction, .05) * scale
    distance = near - np.linalg.norm(heading) * scale - 10
    if not np.isfinite(distance) or distance <= 0:
        raise ValueError("No valid approach to the figurine")
    return {
        "id": "toy_figurine", "name": "小摆件",
        "keywords": ["摆件", "玩具", "玩具摆件", "玩偶", "toy", "toy figurine", "figurine"],
        "turn_deg": float(angle), "distance_cm": float(distance), "stand_off_cm": 10,
        "geometry": {"up": up.tolist(), "start": start.tolist(), "front": front.tolist(),
                     "target": center.tolist(), "stop": (start + direction * distance / scale).tolist()},
        "outline": outline.tolist(), "mask_views": sorted(observations),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--reference-model", type=Path, required=True)
    parser.add_argument("--reference-images", type=Path, required=True)
    parser.add_argument("--gs-job", type=Path, required=True)
    parser.add_argument("--mask-root", type=Path, help="Per-photo cup masks from segment_tabletop.py")
    parser.add_argument("--figurine-mask-root", type=Path, help="Per-photo figurine detections")
    parser.add_argument("--figurine-views", nargs="+", default=(), help="Verified figurine image names")
    parser.add_argument("--objects", type=Path, help="Additional localized objects and their verified image marks")
    parser.add_argument("--output", type=Path, help="New manifest next to gaussians.ply")
    args = parser.parse_args()
    output = args.output or args.gs_job / "navigation_gs.json"
    if output.resolve().parent != args.gs_job.resolve():
        parser.error("--output must be next to gaussians.ply in --gs-job")
    result = build_manifest(args.scene, args.reference_model, args.reference_images, args.gs_job,
                            args.mask_root, args.figurine_mask_root, args.figurine_views, args.objects)
    with output.open("x", encoding="utf-8") as file:
        json.dump(result, file, indent=2)
        file.write("\n")
    print(output.resolve())


if __name__ == "__main__":
    main()
