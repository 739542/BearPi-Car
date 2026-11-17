import ctypes
import json
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from routes import PRESETS, ACTIONS, validate, preview_points


def key_direction(down):
    keys = frozenset(down)
    # Opposing keys and three/four-key combinations stop.
    return {frozenset([4]): 4, frozenset([3]): 3,
            frozenset([5]): 5, frozenset([6]): 6,
            frozenset([4, 5]): 7, frozenset([4, 6]): 8,
            frozenset([3, 5]): 9, frozenset([3, 6]): 'a'}.get(keys, 0)


class Features:
    def send_action(self, number):
        if not self.port:
            raise RuntimeError('串口未连接')
        self.send('AT+CAR=' + str(number))

    def stop_timeout(self, token, mid):
        if token == self.generation and mid == self.motion_id and self.stopping:
            self.compatible = False
            self.status.set('停车回报超时，请检查连接')
            self.update_buttons()

    def keyboard_toggle(self):
        self.key_need_release = True
        if self.key_mode.get():
            if not self.port or not self.compatible or self.runner.active or self.busy:
                self.key_mode.set(False)
                self.log('请先连接 v5，等待当前动作结束，再启用键盘遥控。')
            else:
                self.key_hint.set('松开后按方向键；上/下与左/右组合可沿弧线行驶')
        else:
            self.move('停车')
        self.update_buttons()

    def keyboard_tick(self):
        try:
            if self.key_mode.get():
                focused = self.root.focus_displayof() is not None
                if not focused or not self.port or not self.compatible:
                    self.move('停车')
                    self.key_mode.set(False)
                else:
                    down = [cmd for vk, cmd in ((0x26, 4), (0x28, 3), (0x25, 5), (0x27, 6))
                            if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000]
                    now = time.monotonic()
                    if self.key_need_release:
                        if not down:
                            self.key_need_release = False
                        desired = 0
                    else:
                        desired = key_direction(down)
                    if self.key_action and now - self.key_ack > 0.85:
                        self.move('停车')
                        self.compatible = False
                        self.status.set('键盘控制回报超时，已停止发送运动指令')
                    elif desired != self.key_action or desired and now - self.key_last_send >= 0.15:
                        if desired and not self.key_action:
                            self.key_ack = now
                        self.send('AT+DRIVE=' + str(desired))
                        self.key_action = desired
                        self.key_last_send = now
                        self.key_hint.set('持续行驶中，松键停车' if desired else '已松键，等待下一次方向键')
        except Exception as exc:
            self.log('键盘控制中止：' + str(exc))
            self.disconnect(send_stop=False)
        self.root.after(10, self.keyboard_tick)

    def build_routes(self, parent):
        left = ttk.Frame(parent)
        left.pack(side='left', fill='both', expand=True)
        right = ttk.Frame(parent)
        right.pack(side='right', fill='both', expand=True, padx=(16, 0))
        self.preset = tk.StringVar(value=next(iter(PRESETS)))
        ttk.Combobox(left, textvariable=self.preset, values=list(PRESETS), state='readonly', width=28).pack(anchor='w')
        load = ttk.Button(left, text='载入预设', command=self.load_preset)
        load.pack(anchor='w', pady=5)
        self.route_widgets.append(load)
        self.steps = ttk.Treeview(left, columns=('action', 'count'), show='headings', height=8, selectmode='browse')
        self.steps.heading('action', text='动作')
        self.steps.heading('count', text='次数（每次半秒）')
        self.steps.column('action', width=130)
        self.steps.column('count', width=160)
        self.steps.pack(fill='both', expand=True)
        edit = ttk.Frame(left)
        edit.pack(fill='x', pady=5)
        self.action_var = tk.StringVar(value='前进')
        self.count_var = tk.StringVar(value='2')
        ttk.Combobox(edit, textvariable=self.action_var, values=ACTIONS, state='readonly', width=8).pack(side='left')
        ttk.Spinbox(edit, from_=1, to=20, textvariable=self.count_var, width=5).pack(side='left', padx=5)
        for label, fn in [('添加', self.add_step), ('删除', self.delete_step)]:
            b = ttk.Button(edit, text=label, command=fn)
            b.pack(side='left')
            self.route_widgets.append(b)
        files = ttk.Frame(left)
        files.pack(fill='x')
        for label, fn in [('保存路线', self.save_route), ('读取路线', self.read_route)]:
            b = ttk.Button(files, text=label, command=fn)
            b.pack(side='left', padx=3)
            self.route_widgets.append(b)
        self.run_btn = ttk.Button(left, text='执行路线', command=self.run_route)
        self.run_btn.pack(fill='x', pady=6)
        self.route_status = tk.StringVar(value='待执行')
        ttk.Label(left, textvariable=self.route_status).pack(anchor='w')
        self.progress = ttk.Progressbar(left)
        self.progress.pack(fill='x')
        ttk.Label(right, text='路线示意图（不是实测轨迹）').pack()
        self.canvas = tk.Canvas(right, width=370, height=290, bg='white', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.angle_var = tk.StringVar(value='90')
        ttk.Label(right, text='每次转向的示意角度，仅影响预览').pack()
        ttk.Spinbox(right, from_=5, to=180, textvariable=self.angle_var, width=8, command=self.draw_route).pack()
        ttk.Button(right, text='刷新预览', command=self.draw_route).pack(pady=4)
        ttk.Label(right, text='先短距离校准，再执行整条路线。\n中止后不会自动续跑。').pack()
        self.load_preset()

    def rows(self):
        return validate([(self.steps.item(i, 'values')[0], int(self.steps.item(i, 'values')[1]))
                         for i in self.steps.get_children()])

    def load_preset(self):
        if not self.runner.active:
            self.set_rows(PRESETS[self.preset.get()])

    def set_rows(self, rows):
        rows = validate(rows)
        self.steps.delete(*self.steps.get_children())
        for action, count in rows:
            self.steps.insert('', 'end', values=(action, count))
        self.draw_route()

    def add_step(self):
        try:
            rows = [(self.steps.item(i, 'values')[0], int(self.steps.item(i, 'values')[1]))
                    for i in self.steps.get_children()]
            self.set_rows(rows + [(self.action_var.get(), int(self.count_var.get()))])
        except ValueError as exc:
            messagebox.showerror('步骤无效', str(exc))

    def delete_step(self):
        for i in self.steps.selection():
            self.steps.delete(i)
        self.draw_route()

    def draw_route(self):
        self.canvas.delete('all')
        try:
            angle = float(self.angle_var.get())
            if not 5 <= angle <= 180:
                raise ValueError('示意角度应为5至180度')
            pts, _ = preview_points(self.rows(), angle)
            xs, ys = zip(*pts)
            scale = min(280 / max(1, max(xs) - min(xs)), 220 / max(1, max(ys) - min(ys)))
            mapped = [(40 + (x - min(xs)) * scale, 35 + (y - min(ys)) * scale) for x, y in pts]
            for a, b in zip(mapped, mapped[1:]):
                self.canvas.create_line(*a, *b, fill='#2378be', width=3, arrow='last')
            x, y = mapped[0]
            self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill='#22a06b')
            self.canvas.create_text(x, y + 16, text='起点')
        except (ValueError, TypeError):
            self.canvas.create_text(170, 100, text='请添加有效步骤和示意角度')

    def run_route(self):
        if not self.port or not self.compatible or self.busy or self.key_mode.get() or self.stopping:
            return
        try:
            self.runner.start(self.rows(), time.monotonic())
            self.update_buttons()
        except ValueError as exc:
            messagebox.showerror('路线无效', str(exc))

    def save_route(self):
        try:
            rows = self.rows()
            path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('路线文件', '*.json')])
            if path:
                Path(path).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        except ValueError as exc:
            messagebox.showerror('保存失败', str(exc))

    def read_route(self):
        path = filedialog.askopenfilename(filetypes=[('路线文件', '*.json')])
        if not path:
            return
        try:
            self.set_rows(json.loads(Path(path).read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror('读取失败', str(exc))

    def export_log(self):
        path = filedialog.asksaveasfilename(defaultextension='.txt', filetypes=[('文本日志', '*.txt')])
        if path:
            Path(path).write_text(self.log_view.get('1.0', 'end'), encoding='utf-8')
