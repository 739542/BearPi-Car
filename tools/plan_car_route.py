"""Estimate a turn and straight drive to the blue cup from a COLMAP scene."""

import argparse
import json
from pathlib import Path

import numpy as np
import pycolmap
from scipy.optimize import least_squares

from label_sparse_model import COLORS


def triangulate(reconstruction, observations, field):
    views = {view.name: view for view in reconstruction.images.values()}
    records = []
    rows = []
    for name, annotation in observations.items():
        if name not in views:
            continue
        view = views[name]
        camera = reconstruction.cameras[view.camera_id]
        if annotation["image_size"] != [camera.width, camera.height]:
            raise ValueError(f"Annotation dimensions do not match {name}")
        pixel = np.asarray(annotation[field], dtype=float)
        if pixel.shape != (2,) or not np.all((0 <= pixel) & (pixel < [camera.width, camera.height])):
            raise ValueError(f"Invalid {field} in {name}")
        pose = view.cam_from_world
        if callable(pose):
            pose = pose()
        matrix = pose.matrix()
        x, y = camera.cam_from_img(pixel)
        rows.extend((x * matrix[2] - matrix[0], y * matrix[2] - matrix[1]))
        records.append((name, camera, pose, pixel))

    if len(records) < 2:
        raise ValueError(f"Need at least two registered images for {field}")
    _, _, basis = np.linalg.svd(rows)
    if abs(basis[-1, 3]) < 1e-9:
        raise ValueError(f"Cannot determine a finite 3D point for {field}")
    seed = basis[-1, :3] / basis[-1, 3]

    def residual(point):
        errors = []
        for _, camera, pose, pixel in records:
            local = pose * point
            projected = camera.img_from_cam(local[:2] / local[2])
            errors.extend(projected - pixel)
        return errors

    result = least_squares(residual, seed, loss="soft_l1", f_scale=3.0)
    if not result.success or any((pose * result.x)[2] <= 0 for _, _, pose, _ in records):
        raise ValueError(f"Triangulation failed for {field}")
    errors = np.linalg.norm(np.asarray(residual(result.x)).reshape(-1, 2), axis=1)
    return result.x, dict(zip((item[0] for item in records), errors, strict=True))


def read_cup_points(path):
    with path.open(encoding="ascii") as file:
        if file.readline().strip() != "ply" or file.readline().strip() != "format ascii 1.0":
            raise ValueError("Expected an ASCII PLY from label_sparse_model.py")
        for line in file:
            if line.strip() == "end_header":
                points = np.loadtxt(file, ndmin=2)
                break
        else:
            raise ValueError("Missing PLY end_header")
    if points.shape[1] != 6 or not np.all(np.isfinite(points)):
        raise ValueError("Expected finite XYZ RGB point rows")
    cup = points[np.all(points[:, 3:] == COLORS["blue cup"], axis=1), :3]
    if not len(cup):
        raise ValueError("No blue cup points in the semantic cloud")
    return cup


def calculate_route(front, rear, cup, table_normal, gap_cm, stand_off_cm):
    front, rear, cup = (np.asarray(value, dtype=float) for value in (front, rear, cup))
    normal = np.asarray(table_normal, dtype=float)
    length = np.linalg.norm(normal)
    if not np.isfinite(length) or length == 0:
        raise ValueError("Provide a nonzero upward table normal")
    normal = normal / length
    pivot = (front + rear) / 2

    def planar(vector):
        return vector - (vector @ normal) * normal

    heading = planar(front - pivot)
    cup_center = np.median(cup, axis=0)
    toward_cup = planar(cup_center - pivot)
    if min(np.linalg.norm(heading), np.linalg.norm(toward_cup)) < 1e-8:
        raise ValueError("Degenerate heading or cup direction on the table")
    target_direction = toward_cup / np.linalg.norm(toward_cup)
    turn_deg = np.degrees(np.arctan2(normal @ np.cross(heading, target_direction), heading @ target_direction))

    # Estimate the near cup surface along the approach axis without using the single nearest point.
    near_cup = float(np.quantile((cup - pivot) @ target_direction, 0.05))
    original_gap = near_cup - heading @ target_direction
    if original_gap <= 0:
        raise ValueError("The marked front overlaps the estimated cup clearance")
    cm_per_unit = gap_cm / original_gap
    turn_clearance = (near_cup - np.linalg.norm(heading)) * cm_per_unit
    distance_cm = float(max(0.0, turn_clearance - stand_off_cm))
    return {
        "turn_deg": float(turn_deg),
        "distance_cm": distance_cm,
        "measured_gap_cm": gap_cm,
        "stand_off_cm": stand_off_cm,
        "cm_per_scene_unit": float(cm_per_unit),
        "geometry": {
            "up": normal.tolist(),
            "start": pivot.tolist(),
            "front": (pivot + heading).tolist(),
            "target": cup_center.tolist(),
            "stop": (pivot + target_direction * (distance_cm / cm_per_unit)).tolist(),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--heading", type=Path, required=True, help="Front/rear clicks JSON")
    parser.add_argument("--semantic-ply", type=Path, required=True)
    parser.add_argument("--table-normal", type=float, nargs=3, required=True, metavar=("NX", "NY", "NZ"))
    parser.add_argument("--gap-cm", type=float, required=True, help="Measured car-front to near cup distance")
    endpoint = parser.add_mutually_exclusive_group()
    endpoint.add_argument("--stand-off-cm", type=float, default=10.0)
    endpoint.add_argument("--push-cm", type=float, help="Drive this far past initial contact with the object")
    parser.add_argument("--output", type=Path, required=True, help="New route JSON file")
    parser.add_argument("--scene-name", help="Include dashboard scene metadata (place output beside the PLY)")
    args = parser.parse_args()
    if not np.isfinite(args.gap_cm) or args.gap_cm <= 0:
        parser.error("--gap-cm must be a positive finite distance")
    if not np.isfinite(args.stand_off_cm) or args.stand_off_cm <= 0:
        parser.error("--stand-off-cm must be a positive finite distance")
    if args.push_cm is not None and (not np.isfinite(args.push_cm) or args.push_cm < 0):
        parser.error("--push-cm must be a nonnegative finite distance")
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}")
    if args.scene_name is not None and args.output.resolve().parent != args.semantic_ply.resolve().parent:
        parser.error("With --scene-name, put the output JSON next to the semantic PLY")

    reconstruction = pycolmap.Reconstruction(str(args.model_dir))
    observations = json.loads(args.heading.read_text(encoding="utf-8"))["images"]
    front, front_errors = triangulate(reconstruction, observations, "front_xy")
    rear, rear_errors = triangulate(reconstruction, observations, "rear_xy")
    route = calculate_route(front, rear, read_cup_points(args.semantic_ply), args.table_normal,
                            args.gap_cm, args.stand_off_cm if args.push_cm is None else -args.push_cm)
    if args.push_cm is not None:
        route["push_cm"] = args.push_cm
    if args.scene_name is not None:
        route.update(name=args.scene_name, point_cloud=args.semantic_ply.name)
    for label, errors in (("Front", front_errors), ("Rear", rear_errors)):
        print(f"{label} reprojection: " + ", ".join(f"{name} {error:.1f}px" for name, error in errors.items()))
    print(f"Turn {route['turn_deg']:.2f} degrees (positive is left), drive {route['distance_cm']:.2f} cm")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(route, file, indent=2)
        file.write("\n")
    print(f"Saved {args.output.resolve()}")


if __name__ == "__main__":
    main()
