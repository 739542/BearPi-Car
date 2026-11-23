"""Preview or execute calibrated wheel commands on BearPi-Car v12."""

import argparse
import json
import math
from pathlib import Path
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener


CAR_CONTROL = "http://192.168.4.1/control"
REFRESH_SECONDS = 0.1
BETWEEN_ACTIONS_SECONDS = 0.15


def positive_number(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def load_route(path):
    route = json.loads(path.read_text(encoding="utf-8"))
    turn = float(route["turn_deg"])
    distance = float(route["distance_cm"])
    if not -180 <= turn <= 180 or not math.isfinite(distance) or distance < 0:
        raise ValueError("Invalid turn or distance in route JSON")
    return turn, distance


def route_actions(turn_deg, distance_cm, level):
    actions = []
    if turn_deg > 0:
        actions.append(("turn left", -level, level, turn_deg))
    elif turn_deg < 0:
        actions.append(("turn right", level, -level, -turn_deg))
    if distance_cm > 0:
        actions.append(("forward", level, level, distance_cm))
    return actions


def timed_actions(turn_deg, distance_cm, level, forward_rate, turn_rate):
    return [(name, left, right, amount / (forward_rate if name == "forward" else turn_rate))
            for name, left, right, amount in route_actions(turn_deg, distance_cm, level)]


def car_poster():
    opener = build_opener(ProxyHandler({}))

    def post(body):
        request = Request(CAR_CONTROL, data=body.encode("ascii"), method="POST",
                          headers={"X-Car-Control": "1", "Content-Type": "text/plain"})
        with opener.open(request, timeout=0.35) as response:
            return json.load(response)

    return post


def execute(actions, post, cancel=None):
    def cancelled():
        return cancel is not None and cancel.is_set()

    pause = time.sleep if cancel is None else cancel.wait
    session = None
    sequence = 0
    try:
        if not actions or cancelled():
            return
        session = str(post("arm")["session"])
        if not session.isascii() or not session.isdecimal() or not 1 <= len(session) <= 9:
            raise ValueError("Invalid session returned by the car")
        for index, (name, left, right, seconds) in enumerate(actions):
            if cancelled():
                break
            if index:
                sequence += 1
                post(f"drive {session} {sequence} 0 0")
                pause(BETWEEN_ACTIONS_SECONDS)
                if cancelled():
                    break
            print(f"Executing {name}: {seconds:.3f}s", flush=True)
            end = time.monotonic() + seconds
            while not cancelled() and time.monotonic() < end:
                sent = time.monotonic()
                sequence += 1
                post(f"drive {session} {sequence} {left} {right}")
                pause(max(0, min(sent + REFRESH_SECONDS, end) - time.monotonic()))
    finally:
        if session is not None or cancelled():
            post("stop")
            print("Car accepted stop", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", type=Path, help="Route JSON from plan_car_route.py")
    mode.add_argument("--calibrate", choices=("forward", "left", "right"), help="Run one timed calibration move")
    parser.add_argument("--seconds", type=positive_number, help="Pulse length for --calibrate")
    parser.add_argument("--wheel-level", type=int, default=15, choices=range(1, 31), metavar="1..30")
    parser.add_argument("--forward-cm-per-sec", type=positive_number)
    parser.add_argument("--turn-deg-per-sec", type=positive_number)
    parser.add_argument("--execute", action="store_true", help="Send the previewed commands to the car")
    args = parser.parse_args()

    level = args.wheel_level
    if args.calibrate:
        if args.seconds is None:
            parser.error("--calibrate requires --seconds")
        wheels = {"forward": (level, level), "left": (-level, level), "right": (level, -level)}
        actions = [(args.calibrate, *wheels[args.calibrate], args.seconds)]
    else:
        try:
            turn, distance = load_route(args.plan)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        if (turn and args.turn_deg_per_sec is None) or (distance and args.forward_cm_per_sec is None):
            parser.error("Provide the measured speed for each action in the plan")
        actions = timed_actions(turn, distance, level, args.forward_cm_per_sec, args.turn_deg_per_sec)

    if any(not math.isfinite(action[3]) for action in actions):
        parser.error("The measured rates imply an invalid motion duration")

    for name, left, right, seconds in actions:
        print(f"{name}: left={left:+d}, right={right:+d}, duration={seconds:.3f}s")
    if not args.execute:
        print("Preview only. Add --execute to send the commands to the car.")
        return

    try:
        execute(actions, car_poster())
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, KeyError, ValueError) as exc:
        parser.exit(1, f"Control failed: {exc}\n")


if __name__ == "__main__":
    main()
