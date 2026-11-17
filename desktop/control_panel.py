"""Manual serial controller. Opening the window never opens a serial port."""
import queue
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
from routes import Runner, PRESETS, ACTIONS, validate, preview_points
from panel_features import Features

if sys.platform == 'win32':
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()

BASE = Path(__file__).resolve().parent
import serial
from serial.tools import list_ports

from joystick import Stick
VERSION = 'car-stage1-v6'
COMMANDS = {'前进': 4, '后退': 3, '左转': 5, '右转': 6, '停车': 0}


class Controller(Features):
    def __init__(self, root):
        self.root = root
        root.tk.call('tk', 'scaling', 1.333333)
        self.port = None
        self.generation = 0
        self.compatible = False
        self.busy = False
        self.motion_id = 0
        self.events = queue.Queue()
        self.key_mode = tk.BooleanVar(value=False)
        self.key_action = 0
        self.key_last_send = 0.0
        self.key_ack = 0.0
        self.key_need_release = True
        self.runner = Runner(self.send_action)
        self.route_widgets = []
        self.stopping = False
        self.cooldown_until = 0.0
        root.title('BearPi 小车控制台')
        root.geometry('1120x860')
        root.minsize(1040, 800)
        root.configure(bg='#eef2f7')
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background='#eef2f7')
        style.configure('TLabel', background='#eef2f7', font=('Microsoft YaHei UI', 11))
        style.configure('TButton', font=('Microsoft YaHei UI', 11), padding=9)
        style.configure('Drive.TButton', font=('Microsoft YaHei UI', 19, 'bold'), padding=18)
        style.configure('Stop.TButton', foreground='white', background='#c53030', font=('Microsoft YaHei UI', 19, 'bold'), padding=18)
        wrap = ttk.Frame(root, padding=24)
        wrap.pack(fill='both', expand=True)
        ttk.Label(wrap, text='BearPi 小车控制台', font=('Microsoft YaHei UI', 23, 'bold')).pack(anchor='w')
        ttk.Label(wrap, text='USB 遥控 · 方向键持续行驶 · 路线编排 · 点动 30% PWM').pack(anchor='w', pady=(4, 16))
        bar = ttk.Frame(wrap)
        bar.pack(fill='x')
        ttk.Label(bar, text='串口').pack(side='left')
        self.selected = tk.StringVar()
        self.combo = ttk.Combobox(bar, textvariable=self.selected, width=15, state='readonly')
        self.combo.pack(side='left', padx=10)
        ttk.Button(bar, text='刷新', command=self.refresh).pack(side='left')
        self.connect_btn = ttk.Button(bar, text='连接', command=self.toggle)
        self.connect_btn.pack(side='left', padx=10)
        self.status = tk.StringVar(value='未连接，方向按钮已禁用')
        ttk.Label(wrap, textvariable=self.status, foreground='#245b8f').pack(anchor='w', pady=14)
        tabs = ttk.Notebook(wrap)
        tabs.pack(fill='both', expand=True)
        manual = ttk.Frame(tabs, padding=12)
        route = ttk.Frame(tabs, padding=12)
        tabs.add(manual, text='  手动遥控  ')
        tabs.add(route, text='  路线编排与预览  ')
        tabs.bind('<<NotebookTabChanged>>', lambda e: self.move('停车')
                  if self.key_mode.get() else None)
        analog_page = ttk.Frame(tabs, padding=8)
        tabs.add(analog_page, text='  鼠标摇杆  ')
        self.analog = False
        self.stick = Stick(analog_page, self)
        self.build_routes(route)
        ttk.Checkbutton(manual, text='启用方向键持续遥控（松键停车）', variable=self.key_mode, command=self.keyboard_toggle).pack(pady=6)
        self.key_hint = tk.StringVar(value='仅在本窗口前台时有效；空格停车并退出键盘模式')
        ttk.Label(manual, textvariable=self.key_hint).pack()
        pad = ttk.Frame(manual)
        pad.pack(pady=8)
        self.buttons = {}
        for name, row, col in [('前进', 0, 1), ('左转', 1, 0), ('停车', 1, 1), ('右转', 1, 2), ('后退', 2, 1)]:
            b = ttk.Button(pad, text=name, width=8, style='Stop.TButton' if name == '停车' else 'Drive.TButton', command=lambda n=name: self.move(n))
            b.grid(row=row, column=col, padx=7, pady=5)
            self.buttons[name] = b
        ttk.Label(manual, text='点击一次执行一次。左转和右转采用两轮反向转动。').pack(pady=(12, 4))
        ttk.Label(manual, text='空格键停车；烧录前请断开连接和电机电池。').pack(pady=(0, 12))
        bottom = ttk.Frame(wrap)
        bottom.pack(fill='x', pady=8)
        ttk.Label(bottom, text='运行记录').pack(side='left')
        ttk.Button(bottom, text='导出日志', command=self.export_log).pack(side='right')
        ttk.Button(bottom, text='停车 / 中止路线', command=lambda: self.move('停车')).pack(side='right', padx=8)
        self.log_view = tk.Text(wrap, height=7, bg='#172333', fg='#d9e6f4', font=('Consolas', 10), wrap='word', state='disabled')
        self.log_view.pack(fill='x')
        self.log('尚未连接。连接后只查询版本，不会自动启动电机。')
        self.refresh()
        self.update_buttons()
        root.bind('<space>', lambda e: self.move('停车'))
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(60, self.poll)
        root.after(10, self.keyboard_tick)

    def log(self, text):
        self.log_view.configure(state='normal')
        self.log_view.insert('end', time.strftime('%H:%M:%S ') + text.rstrip() + '\n')
        self.log_view.see('end')
        self.log_view.configure(state='disabled')

    def refresh(self):
        ports = [p.device for p in list_ports.comports()]
        self.combo['values'] = ports
        if not self.selected.get() and ports:
            self.selected.set('COM5' if 'COM5' in ports else ports[0])

    def update_buttons(self):
        for name, b in self.buttons.items():
            enabled = self.port is not None and (name == '停车' or self.compatible and not self.busy and not self.runner.active and not self.key_mode.get() and not self.stopping and time.monotonic() >= self.cooldown_until)
            b.configure(state='normal' if enabled else 'disabled')

        if hasattr(self, 'run_btn'):
            ready = self.port is not None and self.compatible and not self.busy and not self.runner.active and not self.key_mode.get() and not self.stopping and time.monotonic() >= self.cooldown_until
            self.run_btn.configure(state='normal' if ready else 'disabled')
            for widget in self.route_widgets:
                widget.configure(state='disabled' if self.runner.active else 'normal')

    def toggle(self):
        if self.port:
            self.disconnect()
            return
        try:
            if not self.selected.get():
                raise ValueError('请先选择串口')
            self.port = serial.Serial(self.selected.get(), 115200, timeout=0.2, write_timeout=0.5)
            self.generation += 1
            token = self.generation
            self.compatible = False
            self.busy = False
            self.connect_btn.configure(text='断开')
            self.combo.configure(state='disabled')
            self.status.set('已连接，正在查询固件版本')
            threading.Thread(target=self.reader, args=(self.port, token), daemon=True).start()
            self.send('AT+CAR=?')
            self.root.after(2500, lambda: self.version_timeout(token))
        except Exception as exc:
            self.log('连接失败：' + str(exc))
            self.disconnect(send_stop=False)
        self.update_buttons()

    def version_timeout(self, token):
        if token == self.generation and self.port and not self.compatible:
            self.status.set('未确认兼容固件，请先烧录新固件；方向按钮保持禁用')

    def reader(self, port, token):
        pending = ''
        try:
            while port.is_open and token == self.generation:
                data = port.read(port.in_waiting or 1)
                pending += data.decode('utf-8', errors='replace').replace('\r', '\n')
                while '\n' in pending:
                    line, pending = pending.split('\n', 1)
                    if line.strip():
                        self.events.put((token, 'line', line))
        except Exception as exc:
            self.events.put((token, 'error', str(exc)))

    def poll(self):
        while not self.events.empty():
            token, kind, text = self.events.get_nowait()
            if token != self.generation:
                continue
            self.log(text)
            if kind == 'error':
                self.disconnect(send_stop=False)
                continue
            text = text.strip()
            self.runner.receive(text, time.monotonic())
            if text.startswith('[CAR] DRIVE ACK '):
                self.key_ack = time.monotonic()
            if self.runner.failed:
                self.compatible = False
            if text == '[CAR] JOY ACK':
                self.stick.ack = time.monotonic()
            if text in ('[CAR] VERSION car-stage1-v5', '[CAR] VERSION ' + VERSION, '[CAR] VERSION car-stage1-v7', '[CAR] VERSION car-stage1-v8', '[CAR] VERSION car-stage1-v9', '[CAR] VERSION car-stage1-v10', '[CAR] VERSION car-stage1-v11', '[CAR] VERSION car-stage1-v12'):
                self.analog = text.endswith((VERSION, "car-stage1-v7", "car-stage1-v8", "car-stage1-v9", "car-stage1-v10", "car-stage1-v11", "car-stage1-v12"))
                self.compatible = True
                self.status.set('固件已确认：' + text.split()[-1])
            if text in ('[CAR] STOP', '[CAR] STOP error=0'):
                self.busy = False
                self.stopping = False
                self.cooldown_until = time.monotonic() + 0.25
                self.status.set('程序已报告停车')
            if text == 'ERROR' or ('error=' in text and not text.endswith('error=0')) or 'READY - STOPPED' in text:
                self.compatible = False
                self.status.set('请重新连接确认设备状态')
            if 'WATCHDOG STOP' in text:
                self.key_mode.set(False)
                self.key_action = 0
                self.compatible = False
                self.status.set('通信超时，开发板已停车，请重新连接')
            if 'FAULT' in text or 'INIT ERROR' in text:
                self.compatible = False
                self.status.set('程序报告故障，请断开电机电池')
            self.update_buttons()
        try:
            if self.runner.active and self.root.focus_displayof() is None:
                self.move('停车')
                self.runner.message = '窗口失去焦点，路线已中止'
            self.runner.tick(time.monotonic())
        except Exception as exc:
            self.log('路线发送失败：' + str(exc))
            self.disconnect(send_stop=False)
        if self.runner.failed:
            self.compatible = False
        self.route_status.set(self.runner.message)
        self.progress['maximum'] = max(1, self.runner.total)
        self.progress['value'] = self.runner.done
        self.update_buttons()
        self.root.after(60, self.poll)

    def send(self, command):
        self.port.write((command + '\r\n').encode('ascii'))
        self.log('发送 ' + command)

    def move(self, name):
        if name == '停车':
            self.stick.reset()
            self.key_mode.set(False)
            self.key_action = 0
            self.runner.cancel('已中止', stop=False)
        elif self.key_mode.get() or self.runner.active or self.stopping or time.monotonic() < self.cooldown_until:
            return
        if not self.port or (name != '停车' and (not self.compatible or self.busy)):
            return
        try:
            self.send('AT+CAR=' + str(COMMANDS[name]))
            if name == '停车':
                self.stopping = True
                self.busy = True
                self.motion_id += 1
                token, mid = self.generation, self.motion_id
                self.root.after(1800, lambda: self.stop_timeout(token, mid))
            if name != '停车':
                self.busy = True
                self.motion_id += 1
                motion_id = self.motion_id
                self.status.set(name + '指令已发送，等待停车回报')
                token = self.generation
                self.root.after(1800, lambda: self.motion_timeout(token, motion_id))
            self.update_buttons()
        except Exception as exc:
            self.log('发送失败：' + str(exc))
            self.disconnect(send_stop=False)

    def motion_timeout(self, token, motion_id):
        if token == self.generation and motion_id == self.motion_id and self.port and self.busy:
            self.compatible = False
            self.status.set('未收到停车回报，请断开电机电池并重新连接检查')
            self.move('停车')
            self.update_buttons()

    def disconnect(self, send_stop=True):
        self.stick.reset()
        self.analog = False
        self.key_mode.set(False)
        self.key_action = 0
        self.runner.cancel('已断开，路线已取消', stop=False)
        self.stopping = False
        if self.port:
            try:
                if send_stop:
                    self.send('AT+CAR=0')
            except Exception as exc:
                self.log('关闭串口：' + str(exc))
            finally:
                try:
                    self.port.close()
                except Exception:
                    pass
        self.port = None
        self.generation += 1
        self.compatible = False
        self.busy = False
        self.connect_btn.configure(text='连接')
        self.combo.configure(state='readonly')
        self.status.set('未连接，方向按钮已禁用')
        self.update_buttons()

    def close(self):
        self.disconnect()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    Controller(root)
    root.mainloop()
