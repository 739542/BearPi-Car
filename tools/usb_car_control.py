"""Translate the route runner's wheel commands to the car board's AT protocol."""

from time import monotonic, sleep


def send_at(connection, command, expected, timeout=0.20):
    connection.reset_input_buffer()
    connection.write((command + "\r\n").encode("ascii"))
    deadline = monotonic() + timeout
    reply = b""
    while monotonic() < deadline:
        size = connection.in_waiting
        if size:
            reply += connection.read(size)
            if expected in reply:
                return
            if b"\r\nERROR" in reply or reply.startswith(b"ERROR"):
                break
        sleep(0.005)
    raise OSError(f"车板未确认 {command}")


def usb_car_poster(connection):
    send_at(connection, "AT+CAR=?", b"[CAR] VERSION car-stage1-v12", timeout=0.5)
    armed = False

    def post(command):
        nonlocal armed
        if command == "arm":
            # The serial AT protocol has no session token. The gateway serializes motion.
            send_at(connection, "AT+JOY=3030", b"[CAR] JOY ACK")
            armed = True
            return {"session": "1"}
        if command == "stop":
            armed = False
            send_at(connection, "AT+CAR=0", b"[CAR] STOP")
            return {}
        verb, session, _, left, right = command.split()
        if verb != "drive" or session != "1" or not armed:
            raise ValueError("Invalid USB wheel command")
        wheels = (int(left), int(right))
        if any(not -30 <= value <= 30 for value in wheels):
            raise ValueError("Invalid USB wheel level")
        levels = "".join(f"{value + 30:02d}" for value in wheels)
        send_at(connection, "AT+JOY=" + levels, b"[CAR] JOY ACK")
        return {}

    return post
