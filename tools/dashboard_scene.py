"""Load the local scene manifest used by the voice gateway dashboard."""

import hashlib
import json
import math
from pathlib import Path

from run_car_route import load_route


DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"
DEMO_SCENE = DASHBOARD / "demo" / "scene.json"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
    "/vendor/supersplat/index.js": ("vendor/supersplat/index.js", "text/javascript; charset=utf-8"),
    "/vendor/supersplat/index.css": ("vendor/supersplat/index.css", "text/css; charset=utf-8"),
}


def load_dashboard_scene(path):
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("Expected a dashboard scene object")
    name = metadata.get("name")
    filename = metadata.get("point_cloud")
    geometry = metadata.get("geometry")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Scene requires a name")
    if not isinstance(filename, str) or Path(filename).name != filename or Path(filename).suffix.lower() != ".ply":
        raise ValueError("Scene point_cloud must name a PLY file beside the JSON")
    cloud = (path.parent / filename).resolve()
    if cloud.parent != path.resolve().parent or not cloud.is_file():
        raise ValueError(f"Scene point cloud not found beside the manifest: {filename}")
    if not isinstance(geometry, dict):
        raise ValueError("Scene requires route geometry")
    for key in ("up", "start", "front", "target", "stop"):
        if not valid_vector(geometry.get(key)):
            raise ValueError(f"Invalid scene geometry: {key}")
    if not any(geometry["up"]) or geometry["front"] == geometry["start"]:
        raise ValueError("Scene requires a nonzero up vector and heading")
    turn, distance = load_route(path)
    return {
        "name": name,
        "point_cloud_url": "/scene/cloud.ply",
        "geometry": {key: geometry[key] for key in ("up", "start", "front", "target", "stop")},
        "turn_deg": turn,
        "distance_cm": distance,
        "push_cm": metadata.get("push_cm"),
        "cm_per_scene_unit": metadata.get("cm_per_scene_unit"),
    }, cloud


def valid_vector(vector):
    return (isinstance(vector, list) and len(vector) == 3
            and all(type(value) in (int, float) and math.isfinite(value) for value in vector))


def scene_targets(scene):
    cup = {
        "id": "blue_cup", "name": "蓝杯",
        "keywords": ["蓝色杯子", "蓝色杯", "蓝色的杯子", "杯子", "blue cup", "blue_cup", "cup"],
        **{key: scene.get(key) for key in ("turn_deg", "distance_cm", "push_cm", "geometry", "gs_navigation")},
    }
    return [cup, *scene.pop("additional_targets", [])]


def load_navigation_gs(path, scene_path):
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest["scene_sha256"] != hashlib.sha256(scene_path.read_bytes()).hexdigest():
        raise ValueError("The GS alignment was prepared for a different navigation scene")
    cloud = (path.parent / manifest["gaussians"]).resolve()
    if cloud.parent != path.resolve().parent or cloud.suffix.lower() != ".ply" or not cloud.is_file():
        raise ValueError("Expected a Gaussian PLY beside the alignment manifest")
    geometry = manifest["geometry"]
    camera = manifest["camera"]
    cup = manifest["cup_points"]
    outline = manifest.get("cup_outline")
    if (not all(valid_vector(geometry[key]) for key in ("start", "front", "target", "stop"))
            or not all(valid_vector(camera[key]) for key in ("position", "target"))
            or not 0 < camera["fov"] < 180
            or not 3 <= len(cup) <= 20000 or not all(map(valid_vector, cup))
            or (outline is not None and (not 3 <= len(outline) <= 20000 or not all(map(valid_vector, outline))))):
        raise ValueError("Invalid GS route geometry or opening camera")
    targets = manifest.get("targets", [])
    if not isinstance(targets, list):
        raise ValueError("Expected a list of additional targets")
    seen = {"blue_cup"}
    for target in targets:
        target_id = target["id"]
        route = target["geometry"]
        gs = target["gs_navigation"]
        unavailable = target.get("unavailable_reason")
        if unavailable is not None and (not isinstance(unavailable, str) or not unavailable.strip()):
            raise ValueError("Expected an explanation for a target without a route")
        if (not isinstance(target_id, str) or not target_id.isascii()
                or not target_id.replace("_", "").isalnum() or target_id in seen
                or not isinstance(target["name"], str) or not target["name"]
                or not isinstance(target["keywords"], list) or not all(isinstance(word, str) for word in target["keywords"])
                or not all(valid_vector(route[key]) for key in ("up", "start", "front", "target"))
                or not valid_vector(gs["geometry"]["target"])):
            raise ValueError("Invalid additional GS target")
        if unavailable is None and (
            not -180 <= target["turn_deg"] <= 180
            or not math.isfinite(target["distance_cm"]) or target["distance_cm"] <= 0
            or not valid_vector(route["stop"])
            or not all(valid_vector(gs["geometry"][key]) for key in ("start", "front", "stop"))
            or not 3 <= len(gs["cup_outline"]) <= 20000 or not all(map(valid_vector, gs["cup_outline"]))
        ):
            raise ValueError("Invalid additional GS route")
        seen.add(target_id)
    return {
        "gs_url": "/scene/gaussians.ply",
        "gs_camera": camera,
        "gs_navigation": {"geometry": geometry, "cup_points": cup, "cup_outline": outline},
        "additional_targets": targets,
    }, cloud
