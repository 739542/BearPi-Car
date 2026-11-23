"""Stage browser uploads and manage one local PyCOLMAP worker at a time."""

import json
from pathlib import Path
import re
from shutil import rmtree
import subprocess
import sys
from threading import Lock
from uuid import uuid4

from reconstruct_scene import STAGES


MAX_PHOTOS = 40
MAX_PHOTO_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_BYTES = 160 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024
PHOTO_SUFFIXES = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png"}
CLOUD_PATH = re.compile(r"/reconstruction/([0-9a-f]{32})/(scene|gaussians)\.ply\Z")
WORKER = Path(__file__).with_name("reconstruct_scene.py")


class UploadError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def stage_photos(workspace, stream, manifest_size, total_size):
    if not 0 < manifest_size <= MAX_MANIFEST_BYTES or total_size <= manifest_size:
        raise UploadError(400, "照片上传信息不完整。")
    if total_size - manifest_size > MAX_UPLOAD_BYTES:
        raise UploadError(413, "照片总大小超过 160 MB。")
    try:
        manifest = json.loads(stream.read(manifest_size))
        photos = manifest["photos"]
        if not isinstance(photos, list) or not 2 <= len(photos) <= MAX_PHOTOS:
            raise ValueError("请选择 2–40 张照片。")
        name = manifest["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("文件夹名称无效。")
        entries = []
        for photo in photos:
            original = photo["name"]
            if not isinstance(original, str) or not original or len(original) > 240:
                raise ValueError("有照片的文件名无效或过长。")
            suffix = Path(original).suffix.lower()
            size = photo["size"]
            if suffix not in PHOTO_SUFFIXES or type(size) is not int or size < 8:
                raise ValueError("请选择有效的 JPG 或 PNG 照片。")
            if size > MAX_PHOTO_BYTES:
                raise UploadError(413, "单张照片大小超过 20 MB。")
            entries.append((original, suffix, size))
        if sum(size for _, _, size in entries) != total_size - manifest_size:
            raise ValueError("照片大小与上传信息不一致。")
    except (KeyError, TypeError, ValueError) as exc:
        message = str(exc) if type(exc) is ValueError else "照片上传信息无效。"
        raise UploadError(400, message) from exc

    image_dir = workspace / "images"
    image_dir.mkdir()
    filenames = {}
    for index, (original, suffix, size) in enumerate(entries, 1):
        header = stream.read(8)
        is_jpeg = PHOTO_SUFFIXES[suffix] == "jpeg" and header.startswith(b"\xff\xd8\xff")
        is_png = PHOTO_SUFFIXES[suffix] == "png" and header == b"\x89PNG\r\n\x1a\n"
        if not (is_jpeg or is_png):
            raise UploadError(400, "有照片的内容与 JPG 或 PNG 扩展名不符。")
        filename = f"{index:03d}{suffix}"
        filenames[filename] = original
        with (image_dir / filename).open("wb") as photo:
            photo.write(header)
            remaining = size - len(header)
            while remaining:
                chunk = stream.read(min(remaining, 256 * 1024))
                if not chunk:
                    raise UploadError(400, "照片上传中断，请重试。")
                photo.write(chunk)
                remaining -= len(chunk)
    (workspace / "inputs.json").write_text(
        json.dumps({"name": name.strip(), "images": filenames}, ensure_ascii=False), encoding="utf-8",
    )
    train_gs = manifest.get("train_gs", False)
    if type(train_gs) is not bool:
        raise UploadError(400, "GS 训练选项无效。")
    return name.strip(), len(entries), train_gs


class ReconstructionJobs:
    def __init__(self, root, device, brush=None, gs_steps=3000):
        self.root = root
        self.device = device
        self.brush = brush
        self.gs_steps = gs_steps
        self.lock = Lock()
        self.job = None
        self.workspace = None
        self.process = None
        self.closed = False

    def start(self, stream, manifest_size, total_size):
        with self.lock:
            current = self._status()
            if self.closed or current["state"] in ("uploading", "running"):
                raise UploadError(409, "已有重建任务正在运行，请等待完成。")
            job_id = uuid4().hex
            workspace = self.root / job_id
            workspace.mkdir(parents=True)
            self.workspace = workspace
            self.job = {"id": job_id, "state": "uploading"}

        try:
            name, photo_count, train_gs = stage_photos(workspace, stream, manifest_size, total_size)
            command = [sys.executable, "-B", str(WORKER), str(workspace), "--device", self.device]
            if train_gs:
                if self.brush is None:
                    raise UploadError(503, "后端未配置 Brush，请用 --brush 指定训练程序。")
                command += ["--brush", str(self.brush), "--gs-steps", str(self.gs_steps)]
            with self.lock:
                if self.closed:
                    raise UploadError(503, "服务正在关闭。")
                with (workspace / "reconstruction.log").open("wb") as log:
                    self.process = subprocess.Popen(
                        command, stdout=log, stderr=subprocess.STDOUT,
                    )
                self.job.update(state="running", name=name, photos=photo_count)
                return self._status()
        except (UploadError, OSError) as exc:
            with self.lock:
                self.job.update(state="failed", error=str(exc))
            # No worker has used the staged files yet.
            if workspace.resolve().parent == self.root.resolve():
                rmtree(workspace, ignore_errors=True)
            raise

    def status(self):
        with self.lock:
            return dict(self._status(), gs_available=self.brush is not None)

    def _status(self):
        if self.job is None:
            return {"state": "idle"}
        if self.job["state"] != "running":
            return self.job.copy()
        exit_code = self.process.poll()
        if exit_code is None:
            stage = next((item for item in reversed(STAGES) if (self.workspace / item).exists()), "starting")
            backend_file = self.workspace / "sift_backend"
            backend = backend_file.read_text(encoding="ascii") if stage != "starting" else None
            return dict(self.job, stage=stage, sift_backend=backend)

        self.process = None
        try:
            result = json.loads((self.workspace / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = {}
        if exit_code == 0 and result.get("points") and (self.workspace / "scene.ply").is_file():
            self.job.update(
                result, state="completed",
                cloud_url=f"/reconstruction/{self.job['id']}/scene.ply",
            )
            if result.get("gs_ready"):
                self.job["gs_url"] = f"/reconstruction/{self.job['id']}/gaussians.ply"
        else:
            self.job.update(state="failed", error=result.get("error", "重建进程失败，请查看 reconstruction.log。"))
        return self.job.copy()

    def cloud_for(self, path):
        match = CLOUD_PATH.fullmatch(path)
        if match:
            workspace = self.root / match[1]
            cloud = workspace / (match[2] + ".ply")
            if (workspace / "result.json").is_file() and cloud.is_file():
                return cloud
        return None

    def close(self):
        with self.lock:
            self.closed = True
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
