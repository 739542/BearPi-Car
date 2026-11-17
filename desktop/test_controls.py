"""Offline tests: mock serial and key state, never open a board port."""
import unittest
from unittest.mock import patch
import tkinter as tk
from routes import Runner, validate, PRESETS
from control_panel import Controller, VERSION


class RouteTests(unittest.TestCase):
    def test_validation(self):
        for rows in PRESETS.values():
            validate(rows)
        for rows in ([], [('前进', 0)], [('未知', 1)], [('前进', True)], [('前进', 20)] * 5):
            with self.assertRaises(ValueError): validate(rows)

    def test_waits_for_matching_start_and_stop(self):
        sent = []
        r = Runner(sent.append)
        r.start([('前进', 1), ('后退', 1)], 0)
        r.tick(0)
        self.assertEqual(sent, [4])
        r.receive('[CAR] TEST 4 error=0', .1)
        r.tick(.4)
        self.assertEqual(sent, [4])
        r.receive('[CAR] STOP error=0', .6)
        r.tick(.7)
        self.assertEqual(sent, [4])
        r.tick(.9)
        self.assertEqual(sent, [4, 3])
        r.receive('[CAR] TEST 3 error=0', 1)
        r.receive('[CAR] STOP error=0', 1.6)
        r.tick(2)
        self.assertFalse(r.active)
        self.assertEqual(r.done, 2)

    def test_timeout_and_mismatch_stop(self):
        for line in (None, '[CAR] TEST 3 error=0', '[CAR] STOP error=0', 'ERROR'):
            sent = []
            r = Runner(sent.append)
            r.start([('前进', 2)], 0)
            r.tick(0)
            if line: r.receive(line, .1)
            else: r.tick(3)
            self.assertTrue(r.failed)
            self.assertEqual(sent, [4, 0])
            r.tick(10)
            self.assertEqual(sent, [4, 0])


class FakePort:
    def __init__(self): self.sent = []
    def write(self, data): self.sent.append(data)
    def close(self): pass


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch('control_panel.serial.Serial', side_effect=AssertionError('No physical serial allowed'))
        self.guard.start()
        self.root = tk.Tk()
        self.app = Controller(self.root)
        self.port = FakePort()
        self.app.port = self.port
        self.app.compatible = True
        self.app.key_mode.set(True)
        self.focus = patch.object(self.root, 'focus_displayof', return_value=self.root)
        self.focus.start()

    def tearDown(self):
        self.app.port = None
        self.root.destroy()
        self.focus.stop()
        self.guard.stop()

    def tick(self, keys, now):
        with patch('panel_features.time.monotonic', return_value=now), patch(
                'panel_features.ctypes.windll.user32.GetAsyncKeyState',
                side_effect=lambda vk: 0x8000 if vk in keys else 0):
            self.app.keyboard_tick()

    def test_release_arm_heartbeat_and_release_stop(self):
        self.tick([0x26], 1)
        self.assertEqual(self.port.sent, [])  # held before enabling: no auto-start
        self.tick([], 1.1)
        self.tick([0x26], 1.2)
        self.tick([0x26], 1.4)
        self.tick([], 1.5)
        self.assertEqual(self.port.sent, [b'AT+DRIVE=4\r\n', b'AT+DRIVE=4\r\n', b'AT+DRIVE=0\r\n'])

    def test_multiple_keys_and_focus_loss(self):
        self.tick([], 1)
        self.tick([0x25], 1.1)
        self.tick([0x25, 0x27], 1.2)
        self.assertEqual(self.port.sent[-1], b'AT+DRIVE=0\r\n')
        self.focus.stop()
        self.focus = patch.object(self.root, 'focus_displayof', return_value=None)
        self.focus.start()
        self.tick([0x26], 1.3)
        self.assertFalse(self.app.key_mode.get())
        self.assertEqual(self.port.sent[-1], b'AT+CAR=0\r\n')

    def test_lost_reply_stops_and_disables(self):
        self.tick([], 1)
        self.tick([0x28], 1.1)
        self.tick([0x28], 2.1)
        self.assertFalse(self.app.compatible)
        self.assertFalse(self.app.key_mode.get())
        self.assertEqual(self.port.sent[-1], b'AT+CAR=0\r\n')

    def test_old_version_cannot_enable(self):
        self.app.compatible = False
        self.app.keyboard_toggle()
        self.assertFalse(self.app.key_mode.get())
        self.assertEqual(self.port.sent, [])

    def test_diagonal_combinations_and_partial_release(self):
        self.tick([], 1)
        self.tick([0x26, 0x25], 1.1)
        self.assertEqual(self.port.sent[-1], b'AT+DRIVE=7\r\n')
        self.tick([0x26], 1.2)
        self.assertEqual(self.port.sent[-1], b'AT+DRIVE=4\r\n')
        self.tick([0x26, 0x27], 1.3)
        self.assertEqual(self.port.sent[-1], b'AT+DRIVE=8\r\n')
        self.tick([0x28, 0x25], 1.4)
        self.assertEqual(self.port.sent[-1], b'AT+DRIVE=9\r\n')
        self.tick([0x28, 0x27], 1.5)
        self.assertEqual(self.port.sent[-1], b'AT+DRIVE=a\r\n')
        self.tick([0x26, 0x28], 1.6)
        self.assertEqual(self.port.sent[-1], b'AT+DRIVE=0\r\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
