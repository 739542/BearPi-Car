import math
import time
import tkinter as tk
from tkinter import ttk


def wheels(x, y):
    radius = math.hypot(x, y)
    if radius <= .10:
        return 0, 0
    strength = min(1, (radius - .10) / .90)
    a, b = y + x, y - x
    scale = 30 * strength / max(abs(a), abs(b))
    if y < 0:
        # Blend out of the horizontal pivot over the first 10 degrees below it.
        # Deeper in reverse, swap wheel commands to mirror the old rear arcs.
        pivot_band = abs(x) * math.tan(math.radians(10))
        if -y >= pivot_band:
            a, b = b, a
        else:
            reverse = -y / pivot_band
            a, b = a + (b - a) * reverse, b + (a - b) * reverse
    return round(a * scale), round(b * scale)


class Stick:
    def __init__(self, frame, panel):
        self.p = panel
        self.live = self.held = False
        self.pair = (0, 0)
        self.ack = self.sent = 0
        self.last = None
        ttk.Label(frame, text='按住圆内并拖动，松开停车。上推前进，左右偏转改变转弯半径。').pack()
        self.c = tk.Canvas(frame, width=220, height=220, highlightthickness=0)
        self.c.pack()
        self.c.create_oval(10, 10, 210, 210, fill='#e3f1f4', outline='#347989', width=2)
        self.c.create_line(10,110,210,110, fill='#aac5cb')
        self.c.create_line(110,10,110,210, fill='#aac5cb')
        self.knob = self.c.create_oval(97,97,123,123, fill='#176778', outline='')
        self.info = ttk.Label(frame, text='可离线预览；实车摇杆需要 v6 固件')
        self.info.pack()
        self.c.bind('<ButtonPress-1>', self.press)
        self.c.bind('<B1-Motion>', self.drag)
        self.c.bind('<ButtonRelease-1>', lambda e: self.release())
        self.c.bind('<FocusOut>', lambda e: self.release())
        self.c.bind('<Unmap>', lambda e: self.release())
        self.c.after(35,self.tick)

    def press(self,e):
        if math.hypot(e.x-110,e.y-110)>100 or self.held: return
        p=self.p
        self.live=bool(p.port and p.compatible and p.analog and not (p.busy or p.stopping or p.runner.active or p.key_mode.get()) and time.monotonic()>=p.cooldown_until)
        self.held=True
        self.c.focus_set()
        self.c.grab_set()
        self.ack=time.monotonic()
        self.last=None
        self.drag(e)

    def drag(self,e):
        if not self.held: return
        x,y=e.x-110,110-e.y
        k=max(1, math.hypot(x,y)/100)
        x,y=x/k,y/k
        self.c.coords(self.knob,97+x,97-y,123+x,123-y)
        self.pair=wheels(x/100,y/100)
        self.info.configure(text=('%s　左轮指令 %+d　右轮指令 %+d' % (('实车' if self.live else '预览'), *self.pair)))

    def reset(self):
        if self.held: self.c.grab_release()
        self.live=self.held=False
        self.c.coords(self.knob,97,97,123,123)
        self.info.configure(text='已回中；实车摇杆需要 v6 固件')

    def release(self):
        live=self.live
        self.reset()
        if live: self.p.move('停车')

    def tick(self):
        if self.live:
            now=time.monotonic()
            if not self.p.compatible or not self.p.port or self.c.focus_displayof() is None or now-self.ack>0.65:
                self.release()
            elif self.last!=self.pair or now-self.sent>=.15:
                l,r=self.pair
                try:
                    self.p.send(f'AT+JOY={l+30:02d}{r+30:02d}')
                    self.last,self.sent=self.pair,now
                except Exception:
                    self.p.disconnect(send_stop=False)
        self.c.after(35,self.tick)
