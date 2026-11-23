"""Receive voice command IDs and preview or execute BearPi-Car movement."""

import argparse
from contextlib import ExitStack
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import shutil
import sys
from threading import Event, Lock, Thread
from urllib.request import ProxyHandler, Request, build_opener

from dashboard_scene import ASSETS, DASHBOARD, DEMO_SCENE, load_dashboard_scene, load_navigation_gs, scene_targets
from reconstruction_jobs import ReconstructionJobs, UploadError
from run_car_route import car_poster, execute, load_route, positive_number, route_actions, timed_actions
from scene_navigation import SceneNavigation
from usb_car_control import usb_car_poster


FORWARD_ID = 22
BACKWARD_ID = 23
STOP_ID = 24
SERIAL_BAUD_RATE = 115200
SERIAL_READ_SIZE = 256
CAR_TASK_URL = "http://192.168.4.1/tasks/next"
BLUE_CUP_TARGET = "blue_cup"
# The boot logger can prefix a voice line. Require the ID to be the last field.
SERIAL_VOICE_ID = re.compile(rb"\[VOICE\] ID=([0-9]{1,3})\r?\Z")


class VoiceGateway:
    def __init__(self, cup_id, turn, distance, level, actions, post=None, manual_seconds=0.5,
                 targets=None, navigation=None):
        self.cup_id = cup_id
        self.turn = turn
        self.distance = distance
        self.preview = route_actions(turn, distance, level)
        self.actions = actions
        self.targets = targets or {}
        self.navigation = navigation
        self.manual_actions = {
            FORWARD_ID: ("forward", level, level, manual_seconds),
            BACKWARD_ID: ("backward", -level, -level, manual_seconds),
        }
        self.post = post
        self.lock = Lock()
        self.worker = None
        self.cancel = None
        self.state = "idle"
        self.error = None
        self.last_command_id = None
        self.last_target = None
        self.closing = False
        self.car_connection = "not_polled"

    def report_car_connection(self, state):
        with self.lock:
            self.car_connection = state

    def status(self):
        with self.lock:
            steps = []
            for i, (name, left, right, amount) in enumerate(self.preview):
                unit = "cm" if name == "forward" else "deg"
                seconds = self.actions[i][3] if self.actions is not None else None
                steps.append(dict(action=name, left=left, right=right, amount=amount, unit=unit, seconds=seconds))
            return dict(mode="live" if self.post else "preview", state=self.state,
                        blue_cup_id=self.cup_id, last_command_id=self.last_command_id,
                        turn_deg=self.turn, distance_cm=self.distance, steps=steps, error=self.error,
                        car_connection=self.car_connection, last_target=self.last_target,
                        navigation=self.navigation.status() if self.navigation else None)

    def submit(self, command_id):
        return self._submit(command_id)

    def navigate(self, target_id=BLUE_CUP_TARGET, revision=None):
        return self._submit(None, target_id, revision)

    def reset_navigation(self):
        with self.lock:
            if not self.navigation or self.worker is not None or self.closing:
                return 409, {"error": "停车后才能重置小车的起点。"}
            self.navigation.edit_pose("start")
            self.state = "idle"
            self.error = None
            self.last_target = self.last_command_id = None
            return 200, {"navigation": self.navigation.status()}

    def _submit(self, command_id, target_id=None, revision=None):
        with self.lock:
            if self.closing:
                return 503, {"error": "gateway is shutting down"}
            if target_id is None and (type(command_id) is not int
                                   or command_id not in self.manual_actions and command_id not in (self.cup_id, STOP_ID)):
                return 422, {"error": "command_id is not mapped"}
            if target_id is None and command_id == self.cup_id:
                target_id = BLUE_CUP_TARGET
            segment = None
            if self.navigation and target_id in self.navigation.targets:
                if self.navigation.source == "unknown":
                    return 409, {"error": "小车当前位置未确认。请摆回拍摄时的起点，再重置起点。"}
                if command_id is None and revision != str(self.navigation.revision):
                    return 409, {"error": "起点已更新，请核对新路线后再出发。"}
                if self.post and command_id is not None and self.navigation.source != "start":
                    return 409, {"error": "请在网页核对上一段停车位姿后继续实车导航。"}
                segment = self.navigation.plan(target_id)
                if segment is None:
                    return 409, {"error": "小车已在这个目标的计划停车点。"}
                preview, actions = segment["preview"], segment["timed"]
            elif target_id == BLUE_CUP_TARGET:
                preview, actions = self.preview, self.actions
            elif target_id in self.targets:
                preview, actions = self.targets[target_id]
            elif target_id is not None:
                return 404, {"error": "当前场景没有这个导航目标。"}
            else:
                manual = self.manual_actions.get(command_id)
                preview = actions = (manual,) if manual else ()
            if target_id is not None and (not preview or self.post and not actions):
                return 409, {"error": "目标路线尚未就绪，请核对所需的速度标定。"}

            stopping = command_id == STOP_ID
            if self.worker is not None:
                if not stopping:
                    return 409, {"error": "a movement or stop is already running"}
                self.last_command_id = command_id
                self.last_target = None
                self.state = "stopping"
                self.cancel.set()
            else:
                self.last_command_id = command_id
                self.last_target = target_id
                self.state = "stopping" if stopping else "running"
                self.error = None
                self.cancel = Event()
                self.worker = Thread(target=self.run, args=(command_id, self.cancel, preview, actions, target_id, segment))
                self.worker.start()

            event = f"navigation target {target_id}" if command_id is None else f"voice command {command_id}"
            print(f"{event}: {self.state}", flush=True)
            return 202, {"accepted": True, "state": self.state}

    def run(self, command_id, cancel, preview, actions, target_id=None, segment=None):
        stop_only = command_id == STOP_ID
        manual_action = self.manual_actions.get(command_id)
        error = None
        try:
            if self.post is not None:
                if stop_only:
                    self.post("stop")
                else:
                    execute(actions, self.post, cancel)
            elif not stop_only and not cancel.is_set():
                for name, left, right, amount in preview:
                    if manual_action is not None:
                        unit = "s"
                    else:
                        unit = "cm" if name == "forward" else "deg"
                    print(f"[preview] {name}: wheels=({left:+d},{right:+d}), {amount:.2f} {unit}", flush=True)
        except Exception as exc:
            error = str(exc)

        with self.lock:
            self.error = error
            if error is not None:
                self.state = "error"
            elif stop_only or cancel.is_set():
                self.state = "stopped"
            else:
                self.state = "completed" if self.post is not None else "previewed"
            if self.navigation and not stop_only:
                if segment is not None and self.state in ("completed", "previewed"):
                    self.navigation.finish(target_id, segment)
                elif self.post:
                    self.navigation.edit_pose("unknown")
            self.worker = None
            print(f"motion state: {self.state}" + (f" ({error})" if error is not None else ""), flush=True)

    def close(self):
        with self.lock:
            self.closing = True
            worker = self.worker
            if worker is not None:
                self.cancel.set()
        if worker is not None:
            worker.join()


def handler_for(gateway, scene=None, cloud=None, reconstruction=None, gaussians=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def send_headers(self, status, length, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def send_body(self, status, body, content_type):
            self.send_headers(status, len(body), content_type)
            self.wfile.write(body)

        def send_json(self, status, payload):
            self.send_body(status, json.dumps(payload).encode("utf-8"), "application/json")

        def send_file(self, path, content_type):
            try:
                source = path.open("rb")
            except OSError:
                self.send_json(404, {"error": "file unavailable"})
                return
            with source:
                self.send_headers(200, path.stat().st_size, content_type)
                try:
                    shutil.copyfileobj(source, self.wfile, length=256 * 1024)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        def do_GET(self):
            if self.path == "/status":
                self.send_json(200, gateway.status())
            elif self.path in ASSETS:
                filename, content_type = ASSETS[self.path]
                self.send_file(DASHBOARD / filename, content_type)
            elif self.path == "/scene" and scene is not None:
                self.send_json(200, scene)
            elif self.path == "/scene/cloud.ply" and cloud is not None:
                self.send_file(cloud, "application/octet-stream")
            elif self.path == "/scene/gaussians.ply" and gaussians is not None:
                self.send_file(gaussians, "application/octet-stream")
            elif (reconstruction is not None and self.path.startswith("/reconstruction")
                  and self.client_address[0] != "127.0.0.1"):
                self.send_json(403, {"error": "重建功能请在本机导航台使用。"})
            elif reconstruction is not None and self.path == "/reconstruction":
                self.send_json(200, reconstruction.status())
            elif reconstruction is not None and self.path.startswith("/reconstruction/"):
                result = reconstruction.cloud_for(self.path)
                if result is None:
                    self.send_json(404, {"error": "reconstruction not found"})
                else:
                    self.send_file(result, "application/octet-stream")
            else:
                self.send_json(404, {"error": "unknown path"})

        def start_reconstruction(self):
            if (self.client_address[0] != "127.0.0.1"
                    or self.headers.get_content_type() != "application/octet-stream"):
                self.send_json(403, {"error": "请在运行后端的电脑上打开本机导航台提交照片。"})
                return
            try:
                total_size = int(self.headers.get("Content-Length", "0"))
                manifest_size = int(self.headers.get("X-Photo-Manifest-Bytes", "0"))
                self.connection.settimeout(15)
                result = reconstruction.start(self.rfile, manifest_size, total_size)
            except UploadError as exc:
                self.send_json(exc.status, {"error": str(exc)})
                return
            except (TimeoutError, ValueError):
                self.send_json(400, {"error": "上传超时或格式无效，请重新选择照片。"})
                return
            except OSError as exc:
                print(f"[reconstruction] {exc}", file=sys.stderr, flush=True)
                self.send_json(500, {"error": "无法保存照片或启动重建，请检查工作目录。"})
                return
            self.send_json(202, result)

        def do_POST(self):
            if self.path == "/reconstruction" and reconstruction is not None:
                self.start_reconstruction()
                return
            if self.path == "/stop":
                self.send_json(*gateway.submit(STOP_ID))
                return
            if self.path == "/navigation/reset":
                if self.client_address[0] != "127.0.0.1" or self.headers.get("X-Car-Navigate") != "reset":
                    self.send_json(403, {"error": "请在本机导航台确认起点。"})
                else:
                    self.send_json(*gateway.reset_navigation())
                return
            if self.path.startswith("/navigate/"):
                slug = self.path[len("/navigate/"):]
                target_id = slug.replace("-", "_")
                if (self.client_address[0] != "127.0.0.1"
                        or self.headers.get("X-Car-Navigate") != target_id):
                    self.send_json(403, {"error": "请在本机导航台确认出发。"})
                elif not scene or not scene.get("web_navigation_configured"):
                    self.send_json(409, {"error": "当前场景与后端路线未配对。"})
                else:
                    target = next((item for item in scene.get("targets", []) if item["id"] == target_id), None)
                    if target and target.get("unavailable_reason"):
                        self.send_json(409, {"error": target["unavailable_reason"]})
                    else:
                        self.send_json(*gateway.navigate(target_id, self.headers.get("X-Car-Route-Revision")))
                return
            if self.path != "/voice/command":
                self.send_json(404, {"error": "unknown path"})
                return

            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024:
                    raise ValueError("invalid content length")
                self.connection.settimeout(2.0)
                event = json.loads(self.rfile.read(size))
                if not isinstance(event, dict) or type(event.get("command_id")) is not int:
                    raise ValueError("missing integer command_id")
            except (TimeoutError, ValueError):
                self.send_json(400, {"error": "expected JSON with an integer command_id"})
                return
            self.send_json(*gateway.submit(event["command_id"]))

    return Handler


def read_serial_commands(connection, gateway, shutdown):
    partial = b""
    previous_id = None
    label = f"[serial {connection.port}]"
    while not shutdown.is_set():
        try:
            chunk = connection.read(SERIAL_READ_SIZE)
        except OSError as exc:
            print(f"{label} read failed: {exc}. Reconnect the board and restart the gateway.",
                  file=sys.stderr, flush=True)
            return
        if not chunk:
            shutdown.wait(0.02)
            continue
        lines = (partial + chunk).split(b"\n")
        # Keep a bounded suffix across timeouts and discard incomplete lines at shutdown.
        partial = lines.pop()[-SERIAL_READ_SIZE:]
        for line in lines:
            if shutdown.is_set():
                return
            match = SERIAL_VOICE_ID.search(line)
            if match is None:
                continue
            command_id = int(match[1])
            if command_id > 255 or command_id == previous_id:
                continue
            previous_id = command_id
            if command_id == 0:
                continue
            status, result = gateway.submit(command_id)
            outcome = result.get("error", "accepted")
            print(f"{label} ID={command_id}: {outcome} ({status})", flush=True)


def read_car_tasks(url, gateway, shutdown, report_connection=None):
    opener = build_opener(ProxyHandler({}))
    request = Request(url, headers={"X-Car-Control": "1"})
    last_error = None
    connected = False
    while not shutdown.is_set():
        try:
            with opener.open(request, timeout=0.35) as response:
                command_id = None
                if response.status == 200:
                    body = response.read(129)
                    if len(body) > 128:
                        raise ValueError("task response is too large")
                    event = json.loads(body)
                    if not isinstance(event, dict) or type(event.get("command_id")) is not int:
                        raise ValueError("expected an integer command_id")
                    command_id = event["command_id"]
                    if not 1 <= command_id <= 255:
                        raise ValueError("command_id is outside 1..255")
                elif response.status != 204:
                    raise ValueError(f"unexpected HTTP {response.status}")
        except (OSError, ValueError, HTTPException) as exc:
            connected = False
            error = str(exc)
            if error != last_error:
                if report_connection is not None:
                    report_connection("offline")
                print(f"[car poll] {error}", file=sys.stderr, flush=True)
            last_error = error
            shutdown.wait(0.5)
            continue

        if not connected:
            connected = True
            if report_connection is not None:
                report_connection("online")
        if last_error is not None:
            print("[car poll] connection recovered", flush=True)
            last_error = None
        if command_id is not None and not shutdown.is_set():
            status, result = gateway.submit(command_id)
            outcome = result.get("error", "accepted")
            print(f"[car poll] ID={command_id}: {outcome} ({status})", flush=True)
        shutdown.wait(0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, help="Blue-cup route JSON (requires --blue-cup-id)")
    parser.add_argument("--scene", type=Path, help="Dashboard scene JSON with geometry and a neighboring PLY")
    parser.add_argument("--navigation-gs", type=Path, help="Aligned GS manifest from align_navigation_gs.py")
    parser.add_argument("--reconstruction-dir", type=Path, default=DASHBOARD.parent / "local_reconstructions",
                        help="Output folder for local photo reconstruction jobs")
    parser.add_argument("--reconstruction-device", choices=("auto", "cpu", "cuda"), default="auto",
                        help="SIFT device (default: prefer available CUDA COLMAP)")
    parser.add_argument("--brush", type=Path, help="Brush CLI executable for optional GS training")
    parser.add_argument("--gs-steps", type=int, default=2500, help="Brush iterations (default: 2500)")
    parser.add_argument("--blue-cup-id", type=int, help="Trained command ID for the configured navigation route")
    parser.add_argument("--listen", default="127.0.0.1", help="LAN address or 0.0.0.0 for a physical voice board")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--serial-port", help="USB serial port of the voice board, e.g. COM15 (115200 8N1)")
    parser.add_argument("--car-serial-port", help="USB serial port of the car board for --live, e.g. COM4")
    parser.add_argument("--poll-car", nargs="?", const=CAR_TASK_URL, metavar="URL",
                        help=f"Poll the car's task mailbox (default URL: {CAR_TASK_URL})")
    parser.add_argument("--wheel-level", type=int, default=20, choices=range(1, 31), metavar="1..30")
    parser.add_argument("--manual-seconds", type=positive_number, default=0.5,
                        help="Duration of each forward/backward voice command (default: 0.5)")
    parser.add_argument("--forward-cm-per-sec", type=positive_number)
    parser.add_argument("--turn-deg-per-sec", type=positive_number, help="Calibrated turn speed for the base route")
    parser.add_argument("--left-turn-deg-per-sec", type=positive_number, help="Calibrated left-turn speed for multi-leg routes")
    parser.add_argument("--right-turn-deg-per-sec", type=positive_number, help="Calibrated right-turn speed for additional targets")
    parser.add_argument("--live", action="store_true", help="Send wheel commands to the physical car")
    args = parser.parse_args()
    if not 1 <= args.gs_steps <= 30000:
        parser.error("--gs-steps must be between 1 and 30000")
    brush = args.brush
    if brush is None:
        executable = shutil.which("brush_app") or shutil.which("brush")
        brush = Path(executable) if executable else None
    if brush is not None:
        brush = brush.resolve()
        if not brush.is_file():
            parser.error("--brush must point to an existing executable")

    if args.plan is not None and args.blue_cup_id is None:
        parser.error("--plan requires --blue-cup-id")
    plan = args.plan
    if args.navigation_gs is not None and plan is None:
        plan = args.scene or DEMO_SCENE
    if args.blue_cup_id is not None and plan is None:
        if args.scene is None:
            parser.error("--blue-cup-id requires --plan or explicit --scene")
        plan = args.scene
    if args.blue_cup_id is not None and (
        not 1 <= args.blue_cup_id <= 255 or args.blue_cup_id in (FORWARD_ID, BACKWARD_ID, STOP_ID)
    ):
        parser.error("Provide a valid blue-cup command ID distinct from 22, 23 and 24")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.poll_car and args.serial_port:
        parser.error("Choose one physical input: --poll-car or --serial-port")
    if args.car_serial_port and not args.live:
        parser.error("--car-serial-port requires --live")
    if args.car_serial_port and args.serial_port and args.car_serial_port.upper() == args.serial_port.upper():
        parser.error("Use distinct USB ports for the car and the voice board")
    if args.poll_car:
        try:
            request = Request(args.poll_car)
            if request.type not in ("http", "https") or not request.host:
                raise ValueError("expected an HTTP URL with a host")
        except ValueError as exc:
            parser.error(f"Invalid --poll-car URL: {exc}")
    turn = distance = 0.0
    actions = None
    if plan is not None:
        try:
            turn, distance = load_route(plan)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            parser.error(str(exc))

        calibrated = (not turn or args.turn_deg_per_sec is not None) and (
            not distance or args.forward_cm_per_sec is not None
        )
        if args.live and not calibrated:
            parser.error("--live requires measured speeds for the route actions")
        if calibrated:
            actions = timed_actions(turn, distance, args.wheel_level, args.forward_cm_per_sec, args.turn_deg_per_sec)
            if any(not math.isfinite(action[3]) for action in actions):
                parser.error("The measured speeds imply an invalid motion duration")

    scene_path = args.scene or DEMO_SCENE
    gaussians = None
    try:
        scene, cloud = load_dashboard_scene(scene_path)
        if args.navigation_gs is not None:
            gs_scene, gaussians = load_navigation_gs(args.navigation_gs, scene_path)
            scene.update(gs_scene)
    except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        parser.error(f"Could not load dashboard scene: {exc}")
    same_route = plan is not None and plan.resolve() == scene_path.resolve()
    scene["voice_navigation_configured"] = args.blue_cup_id is not None and same_route
    scene["web_navigation_configured"] = gaussians is not None and same_route
    scene["targets"] = scene_targets(scene)
    target_plans = {}
    for item in scene["targets"]:
        if item.get("unavailable_reason"):
            item.update(live_ready=False, web_ready=False)
            continue
        if item["id"] == BLUE_CUP_TARGET:
            item.update(live_ready=bool(actions), web_ready=scene["web_navigation_configured"])
            continue
        angle, length = item["turn_deg"], item["distance_cm"]
        turn_rate = args.right_turn_deg_per_sec if angle < 0 else args.turn_deg_per_sec
        timed = None
        if args.forward_cm_per_sec and (not angle or turn_rate):
            timed = timed_actions(angle, length, args.wheel_level, args.forward_cm_per_sec, turn_rate)
            if any(not math.isfinite(action[3]) for action in timed):
                parser.error("The measured speeds imply an invalid motion duration")
        item.update(live_ready=bool(timed), web_ready=scene["web_navigation_configured"])
        target_plans[item["id"]] = (route_actions(angle, length, args.wheel_level), timed)

    with ExitStack() as stack:
        post = car_poster() if args.live else None
        if args.car_serial_port:
            try:
                import serial
                car = serial.Serial(port=None, baudrate=SERIAL_BAUD_RATE, timeout=0, write_timeout=0.2)
                car.dtr = car.rts = False
                car.port = args.car_serial_port
                stack.callback(car.close)
                car.open()
                post = usb_car_poster(car)
            except (ImportError, OSError, ValueError) as exc:
                parser.error(f"Could not initialize the car on {args.car_serial_port}: {exc}")
        navigation = None
        scale = scene["cm_per_scene_unit"]
        if scene["web_navigation_configured"] and type(scale) in (float, int) and math.isfinite(scale) and scale > 0:
            left = args.left_turn_deg_per_sec or (args.turn_deg_per_sec if turn >= 0 else None)
            right = args.right_turn_deg_per_sec or (args.turn_deg_per_sec if turn < 0 else None)
            navigation = SceneNavigation(scene, args.wheel_level, args.forward_cm_per_sec, left, right)
        gateway = VoiceGateway(args.blue_cup_id, turn, distance, args.wheel_level, actions,
                               post, args.manual_seconds, target_plans, navigation)
        if args.car_serial_port:
            gateway.report_car_connection("usb")
        elif args.poll_car:
            gateway.report_car_connection("connecting")
        reconstruction = ReconstructionJobs(args.reconstruction_dir.resolve(), args.reconstruction_device,
                                           brush, args.gs_steps)
        stack.callback(reconstruction.close)
        server = stack.enter_context(ThreadingHTTPServer(
            (args.listen, args.port), handler_for(gateway, scene, cloud, reconstruction, gaussians),
        ))
        shutdown = Event()
        reader = None
        if args.serial_port:
            try:
                import serial
            except ImportError:
                parser.error("--serial-port requires pyserial. Install the project's requirements.txt.")
            try:
                # Read only buffered bytes. CH340 on Windows can fail an overlapped
                # timed read that asks for more bytes than are currently available.
                connection = stack.enter_context(serial.Serial(args.serial_port, SERIAL_BAUD_RATE, timeout=0))
                connection.reset_input_buffer()
            except (OSError, ValueError) as exc:
                parser.error(f"Could not open {args.serial_port}: {exc}")
            reader = Thread(target=read_serial_commands, args=(connection, gateway, shutdown))
            reader.start()
            print(f"[serial {args.serial_port}] listening at {SERIAL_BAUD_RATE} 8N1", flush=True)
        elif args.poll_car:
            reader = Thread(target=read_car_tasks,
                            args=(args.poll_car, gateway, shutdown,
                                  None if args.car_serial_port else gateway.report_car_connection))
            reader.start()
            print(f"[car poll] reading {args.poll_car}", flush=True)
        mode = "live" if args.live else "preview"
        cup_info = f", blue-cup ID {args.blue_cup_id}" if args.blue_cup_id is not None else ""
        print(f"BearPi gateway listening on http://{args.listen}:{args.port} ({mode}{cup_info})", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            shutdown.set()
            if reader is not None:
                reader.join()
            gateway.close()


if __name__ == "__main__":
    main()
