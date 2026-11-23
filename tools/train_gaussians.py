"""Train and export a browser-ready Gaussian splat with Brush."""

import os
from pathlib import Path
from shutil import copy2
import subprocess


def train_gaussians(workspace, sparse_model, image_names, brush, steps):
    dataset = workspace / "gs_input"
    images = dataset / "images"
    sparse = dataset / "sparse" / "0"
    images.mkdir(parents=True)
    sparse.mkdir(parents=True)
    for name in image_names:
        source = workspace / "images" / name
        target = images / name
        try:
            os.link(source, target)
        except OSError:
            copy2(source, target)
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        copy2(sparse_model / name, sparse / name)

    partial = workspace / "gaussians-training.ply"
    exported = workspace / "gaussians.ply"
    env = os.environ.copy()
    env.setdefault("RUST_LOG", "brush_cli=info,wgpu_core=warn")
    command = [
        str(brush), str(dataset), "--total-steps", str(steps), "--max-resolution", "1200",
        "--max-splats", "180000", "--refine-every", "50",
        "--growth-grad-threshold", "0.00002", "--growth-select-fraction", "0.3",
        "--export-every", str(steps), "--export-path", str(workspace), "--export-name", partial.name,
    ]
    with (workspace / "brush.log").open("wb") as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
    with partial.open("rb") as data:
        header = data.read(4096).split(b"end_header")[0]
    if not all(field in header for field in (b"property float opacity", b"property float scale_0",
                                             b"property float rot_0", b"property float f_dc_0")):
        raise ValueError("Brush 未输出有效的 Gaussian PLY。")
    partial.replace(exported)
