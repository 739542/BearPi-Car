"""Build a COLMAP sparse scene from a staged folder of photographs."""

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from train_gaussians import train_gaussians


STAGES = ("features", "matching", "mapping", "export", "gaussians")


def write_result(workspace, result):
    temporary = workspace / (uuid4().hex + ".json")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(workspace / "result.json")


def sift_backend(colmap, device):
    if device == "cpu":
        return "cpu", None
    if colmap.has_cuda:
        return "pycolmap-cuda", None
    cli = shutil.which("colmap") or shutil.which("COLMAP.bat")
    if cli:
        executable = Path(cli)
        if executable.suffix.lower() == ".bat":
            executable = executable.parent / "bin" / "colmap.exe"
        if executable.is_file():
            result = subprocess.run([str(executable), "help"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if b"with CUDA" in result.stdout:
                return "colmap-cuda", executable
    if device == "cuda":
        raise ValueError("未找到 CUDA 版 COLMAP 或 PyCOLMAP。请检查 COLMAP 是否已加入 PATH。")
    return "cpu", None


def run_colmap(executable, command, *args):
    env = os.environ.copy()
    plugins = executable.parent.parent / "plugins"
    if plugins.is_dir():
        env["QT_PLUGIN_PATH"] = os.pathsep.join(filter(None, (str(plugins), env.get("QT_PLUGIN_PATH"))))
    try:
        subprocess.run([str(executable), command, *map(str, args)], env=env, check=True)
    except subprocess.CalledProcessError as exc:
        raise ValueError("CUDA SIFT 失败。请查看 reconstruction.log 中的 COLMAP 错误。") from exc


def reconstruct(workspace, device, brush=None, gs_steps=3000):
    import pycolmap as colmap

    images = workspace / "images"
    database = workspace / "database.db"
    sparse = workspace / "sparse"
    sparse.mkdir()
    threads = min(4, os.cpu_count() or 1)
    backend, executable = sift_backend(colmap, device)
    (workspace / "sift_backend").write_text(backend, encoding="ascii")
    (workspace / "features").touch()
    if executable:
        run_colmap(executable, "feature_extractor", "--database_path", database, "--image_path", images,
                   "--SiftExtraction.use_gpu", "1", "--SiftExtraction.num_threads", threads)
    else:
        colmap.extract_features(
            str(database), str(images), device=colmap.Device.cuda if backend == "pycolmap-cuda" else colmap.Device.cpu,
            sift_options=colmap.SiftExtractionOptions(num_threads=threads),
        )
    (workspace / "matching").touch()
    if executable:
        run_colmap(executable, "exhaustive_matcher", "--database_path", database,
                   "--SiftMatching.use_gpu", "1", "--SiftMatching.num_threads", threads)
    else:
        colmap.match_exhaustive(
            str(database), device=colmap.Device.cuda if backend == "pycolmap-cuda" else colmap.Device.cpu,
            sift_options=colmap.SiftMatchingOptions(num_threads=threads),
        )
    (workspace / "mapping").touch()
    models = colmap.incremental_mapping(
        str(database), str(images), str(sparse),
        options=colmap.IncrementalPipelineOptions(num_threads=threads),
    )
    if not models:
        raise ValueError("未能建立稀疏模型。请多拍几张有共同画面、视角不同、清晰的照片。")

    index, model = max(models.items(), key=lambda pair: (pair[1].num_reg_images(), pair[1].num_points3D()))
    if model.num_reg_images() < 2 or not model.num_points3D():
        raise ValueError("有效相机位姿或三维点太少。请增加有重叠的照片后重试。")

    (workspace / "export").touch()
    model.export_PLY(str(workspace / "scene.ply"))
    first_view = min(model.images.values(), key=lambda image: image.name)
    pose = first_view.cam_from_world
    if callable(pose):
        pose = pose()
    right, down, _ = pose.rotation.matrix()
    result = {
        "registered": model.num_reg_images(),
        "points": model.num_points3D(),
        "sparse_model": f"sparse/{index}",
        "sift_backend": backend,
        "view_up": (-down).tolist(),
        "view_right": right.tolist(),
    }
    if brush:
        (workspace / "gaussians").touch()
        try:
            train_gaussians(workspace, sparse / str(index), [image.name for image in model.images.values()],
                            brush, gs_steps)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"Brush failed: {exc}", file=sys.stderr)
            result["gs_error"] = "GS 训练失败。稀疏点云仍可查看，详情见任务目录的 brush.log。"
        else:
            # SuperSplat rotates PLY assets by 180 degrees around Z.
            center = first_view.projection_center()
            direction = first_view.viewing_direction()
            depths = [(point.xyz - center) @ direction for point in model.points3D.values()]
            depth = float(sorted(depths)[len(depths) // 2])
            camera = model.cameras[first_view.camera_id]
            vfov = math.degrees(2 * math.atan(camera.height / (2 * camera.focal_length_y)))
            target = center + max(depth, 1e-6) * direction
            result.update(gs_ready=True, gs_steps=gs_steps, gs_camera={
                "position": [-float(center[0]), -float(center[1]), float(center[2])],
                "target": [-float(target[0]), -float(target[1]), float(target[2])],
                "fov": vfov,
            })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="Job directory containing images/")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--brush", type=Path, help="Brush executable for optional 3DGS training")
    parser.add_argument("--gs-steps", type=int, default=3000)
    args = parser.parse_args()
    workspace = args.workspace
    try:
        result = reconstruct(workspace, args.device, args.brush, args.gs_steps)
    except ImportError as exc:
        print(exc, file=sys.stderr)
        write_result(workspace, {"error": "重建环境缺少 PyCOLMAP，请用已安装 PyCOLMAP 的 Python 启动后端。"})
        return 1
    except ValueError as exc:
        write_result(workspace, {"error": str(exc)})
        return 1
    write_result(workspace, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
