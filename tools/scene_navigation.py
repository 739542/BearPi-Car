"""Plan consecutive straight segments from the estimated car pose."""

import math

from run_car_route import route_actions, timed_actions


def subtract(a, b):
    return [x - y for x, y in zip(a, b)]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    x, y, z = a
    i, j, k = b
    return [y * k - z * j, z * i - x * k, x * j - y * i]


def front_at(start, stop, old_front):
    heading_length = math.hypot(*subtract(old_front, start))
    displacement = subtract(stop, start)
    length = math.hypot(*displacement)
    return [x + heading_length * d / length for x, d in zip(stop, displacement)] if length else old_front


class SceneNavigation:
    """Used under the gateway's motion lock, including route generation."""

    def __init__(self, scene, level, forward_rate, left_rate, right_rate):
        self.targets = {item["id"]: item for item in scene["targets"] if item["web_ready"]}
        self.scale = scene["cm_per_scene_unit"]
        self.level = level
        self.forward_rate = forward_rate
        self.left_rate = left_rate
        self.right_rate = right_rate
        self.revision = 0
        self.source = "start"
        self.pose = None

    def edit_pose(self, source, pose=None):
        self.source = source
        self.pose = pose
        self.revision += 1

    def plan(self, target_id):
        target = self.targets.get(target_id)
        if not target or self.source == "unknown" or target_id == self.source:
            return None
        geometry = target["geometry"].copy()
        view = target["gs_navigation"]["geometry"].copy()
        turn, distance = target["turn_deg"], target["distance_cm"]
        if self.pose is not None:
            local_pose, gs_pose = self.pose
            geometry.update(local_pose)
            view.update(gs_pose)
            up = geometry["up"]
            up = [value / math.hypot(*up) for value in up]
            delta = subtract(geometry["stop"], geometry["start"])
            delta = [value - dot(delta, up) * axis for value, axis in zip(delta, up)]
            distance = math.hypot(*delta) * self.scale
            if distance < 0.1:
                return None
            heading = subtract(geometry["front"], geometry["start"])
            turn = math.degrees(math.atan2(dot(cross(heading, delta), up), dot(heading, delta)))

        preview = route_actions(turn, distance, self.level)
        turn_rate = self.right_rate if turn < 0 else self.left_rate
        timed = None
        if (not distance or self.forward_rate) and (not turn or turn_rate):
            timed = timed_actions(turn, distance, self.level, self.forward_rate, turn_rate)
            if any(not math.isfinite(step[3]) for step in timed):
                timed = None
        return {
            "turn_deg": turn, "distance_cm": distance, "geometry": geometry, "gs_geometry": view,
            "live_ready": bool(timed), "preview": preview, "timed": timed,
        }

    def finish(self, target_id, route):
        poses = []
        for geometry in (route["geometry"], route["gs_geometry"]):
            poses.append({
                "start": geometry["stop"],
                "front": front_at(geometry["start"], geometry["stop"], geometry["front"]),
            })
        self.edit_pose(target_id, poses)

    def status(self):
        routes = {}
        for target_id in self.targets:
            route = self.plan(target_id)
            if route:
                routes[target_id] = {key: value for key, value in route.items() if key not in ("preview", "timed")}
        return {"source": self.source, "revision": self.revision, "routes": routes}
