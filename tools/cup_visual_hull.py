"""Recover the blue cup's outer shape from registered image masks."""

import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import ConvexHull

from label_sparse_model import choose_masks


def load_cup_views(reconstruction, pairs, mask_root):
    views = []
    for reference, view in pairs:
        name = Path(reference.name).name
        folder = mask_root / name
        manifest = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        camera = reconstruction.cameras[view.camera_id]
        if manifest["image"] != name or (manifest["width"], manifest["height"]) != (camera.width, camera.height):
            raise ValueError(f"Cup mask metadata does not match the registered image: {name}")
        instance = choose_masks(manifest["instances"]).get("blue cup")
        if instance is None:
            continue
        with Image.open(folder / instance["mask"]) as image:
            mask = np.asarray(image.convert("L")) > 0
        if mask.shape != (camera.height, camera.width) or not mask.any():
            raise ValueError(f"Empty or incorrectly sized cup mask: {name}")
        # Small tolerance for reconstruction and mask boundary errors.
        mask = ndimage.binary_dilation(mask, iterations=4)
        pose = view.cam_from_world
        views.append((camera, pose() if callable(pose) else pose, mask))
    if len(views) < 3:
        raise ValueError("Need blue-cup masks in at least three shared registered photos")
    return views


def cup_visual_hull(seeds, views):
    low, high = np.quantile(seeds, [.02, .98], axis=0)
    center = (low + high) / 2
    # The sparse seeds locate the cup. Give the masks room to recover untracked surfaces.
    span = max(high - low)
    for factor, samples in ((.95, 80), (1.5, 108), (2.2, 128)):
        boundary = masked_cup_grid(center, span * factor, samples, views)
        if boundary is not None:
            hull = ConvexHull(boundary)
            return np.round(boundary[hull.vertices], decimals=5)
    raise ValueError("Cup mask volume reaches the reconstruction bounds")


def masked_cup_grid(center, halfspan, samples, views):
    offsets = np.linspace(-halfspan, halfspan, samples)
    grid = np.stack(np.meshgrid(*(axis + offsets for axis in center), indexing="ij"), axis=-1)
    xyz = grid.reshape(-1, 3)
    active = np.arange(len(xyz))
    for camera, pose, mask in views:
        matrix = pose.matrix()
        local = xyz[active] @ matrix[:, :3].T + matrix[:, 3]
        facing = local[:, 2] > 0
        active = active[facing]
        local = local[facing]
        pixels = camera.img_from_cam(local[:, :2] / local[:, 2, None])
        inside = ((pixels[:, 0] >= 0) & (pixels[:, 0] < camera.width)
                  & (pixels[:, 1] >= 0) & (pixels[:, 1] < camera.height))
        x, y = pixels[inside].astype(int).T
        active = active[inside][mask[y, x]]
    occupancy = np.zeros(grid.shape[:3], dtype=bool)
    occupancy.flat[active] = True
    labels, _ = ndimage.label(occupancy)
    counts = np.bincount(labels.ravel())
    counts[0] = 0
    if counts.max() < 20:
        raise ValueError("Cup masks did not yield a consistent 3D volume")
    component = labels == int(counts.argmax())
    if any(np.take(component, [0, -1], axis=axis).any() for axis in range(3)):
        return None
    return grid[component & ~ndimage.binary_erosion(component)]
