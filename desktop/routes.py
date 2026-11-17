"""Bounded action plans and a reply-driven runner for existing v3 firmware."""
import math
import re

COMMANDS = {'前进': 4, '后退': 3, '左转': 5, '右转': 6}
ACTIONS = tuple(COMMANDS) + ('等待',)
PRESETS = {
    '直线往返': [('前进', 2), ('等待', 2), ('后退', 2)],
    '方形路线（需校准转角）': [('前进', 2), ('右转', 1)] * 4,
    '折线路线（需校准转角）': [('前进', 2), ('右转', 1), ('前进', 2), ('左转', 1), ('前进', 2)],
    '左右转向展示': [('左转', 1), ('等待', 2), ('右转', 1)],
}


def validate(rows):
    if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= 40:
        raise ValueError('路线需要 1 至 40 个步骤')
    result = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError('每步需要动作名称和次数')
        action, count = row
        if action not in ACTIONS or type(count) is not int or not 1 <= count <= 20:
            raise ValueError('动作必须有效，每步次数为 1 至 20 的整数')
        result.append((action, count))
    if sum(n for _, n in result) > 80:
        raise ValueError('整条路线最多 80 次，总动作时间最多 40 秒')
    return result


def preview_points(rows, turn_degrees):
    """Drawing units are arbitrary, not measured distance."""
    x, y, angle = 0.0, 0.0, -math.pi / 2
    points = [(x, y)]
    for action, count in validate(rows):
        for _ in range(count):
            if action in ('前进', '后退'):
                sign = 1 if action == '前进' else -1
                x += sign * math.cos(angle)
                y += sign * math.sin(angle)
                points.append((x, y))
            elif action in ('左转', '右转'):
                angle += math.radians(turn_degrees) * (-1 if action == '左转' else 1)
    return points, angle


class Runner:
    """Caller supplies monotonic time; no background timers or serial ownership."""
    def __init__(self, send):
        self.send = send
        self.active = False
        self.pending = None
        self.started = False
        self.done = 0
        self.total = 0
        self.message = '待执行'
        self.failed = False

    def start(self, rows, now):
        if self.active:
            raise ValueError('已有路线正在执行')
        self.plan = [(i, a) for i, (a, n) in enumerate(validate(rows)) for _ in range(n)]
        self.total = len(self.plan)
        self.done = 0
        self.pending = None
        self.started = False
        self.failed = False
        self.active = True
        self.ready_at = now
        self.message = '准备执行'

    def cancel(self, reason='已中止', stop=True):
        self.active = False
        self.pending = None
        self.started = False
        self.message = reason
        if stop:
            self.send(0)

    def fail(self, reason):
        self.failed = True
        self.cancel(reason)

    def tick(self, now):
        if not self.active:
            return
        if self.pending == '等待':
            if now >= self.deadline:
                self.complete(now)
            return
        if self.pending is not None:
            if now >= self.deadline:
                self.fail('动作回报超时，路线已中止')
            return
        if now < self.ready_at:
            return
        if self.done == self.total:
            self.active = False
            self.message = '路线指令执行完成'
            return
        index, action = self.plan[self.done]
        self.message = f'步骤 {index + 1} · {action} · {self.done + 1}/{self.total}'
        self.pending = action
        self.started = False
        self.deadline = now + (0.5 if action == '等待' else 2.5)
        if action != '等待':
            self.send(COMMANDS[action])

    def complete(self, now):
        self.done += 1
        self.pending = None
        self.started = False
        # Firmware prints STOP just before clearing busy. Allow it to settle.
        self.ready_at = now + 0.25

    def receive(self, line, now):
        if not self.active:
            return
        if line == 'ERROR' or 'FAULT' in line or 'INIT ERROR' in line or 'READY - STOPPED' in line:
            self.fail('设备拒绝指令、复位或故障，路线已中止')
            return
        if self.pending not in COMMANDS:
            return
        match = re.fullmatch(r'\[CAR\] TEST (\d+) error=(\d+)', line)
        if match:
            if int(match[1]) != COMMANDS[self.pending] or int(match[2]) != 0:
                self.fail('动作回报不匹配，路线已中止')
            else:
                self.started = True
        elif line == '[CAR] STOP error=0' and self.started:
            self.complete(now)
        elif line.startswith('[CAR] STOP'):
            self.fail('停车回报异常，路线已中止')
