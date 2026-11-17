"""BearPi v6 phone controller. Bind to the PC address reachable from the phone."""
import argparse
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop"))
from joystick import wheels
import serial


class Bridge:
    def __init__(self, port):
        self.port = port
        self.lock = threading.Lock()
        self.owner = None
        self.until = 0
        self.seq = -1
        self.token = secrets.token_urlsafe(15)

    def command(self, command, expected):
        self.port.reset_input_buffer()
        self.port.write((command + '\r\n').encode('ascii'))
        deadline = time.monotonic() + .55
        while time.monotonic() < deadline:
            line = self.port.readline().strip()
            if line == expected:
                return
            if line in (b'ERROR', b'[CAR] FAULT'):
                break
        raise ValueError('小车未确认指令，请检查 USB 连接')

    def stop(self):
        self.owner = None
        self.command('AT+CAR=0', b'[CAR] STOP')

    def run(self, data):
        with self.lock:
            now = time.monotonic()
            if self.owner and now >= self.until:
                self.stop()
            action = data.get('action')
            if action == 'status':
                return {'version': 'v6', 'busy': bool(self.owner)}
            if action == 'stop':
                self.stop()
                return {}
            if action == 'arm':
                if self.owner:
                    raise ValueError('已有操作进行中')
                self.owner = secrets.token_urlsafe(10)
                self.seq = -1
                self.until = now + .55
                return {'session': self.owner}
            if action != 'drive' or not self.owner or self.owner != data.get('session'):
                raise ValueError('操作已结束，请松手后重新操作')
            seq = data.get('seq')
            if type(seq) is not int or seq <= self.seq:
                raise ValueError('指令已过期')
            x, y = data.get('x'), data.get('y')
            if not all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=1 for v in (x,y)):
                raise ValueError('操控量超出范围')
            left,right = wheels(x,y) if abs(y)>.07 else (0,0)
            try:
                self.command(f'AT+JOY={left+30:02}{right+30:02}',b'[CAR] JOY ACK')
            except Exception:
                self.stop()
                raise
            self.seq = seq
            self.until = now + .55
            return {'left':left,'right':right}

    def watchdog(self):
        while True:
            time.sleep(.04)
            with self.lock:
                if self.owner and time.monotonic() >= self.until:
                    try:
                        self.stop()
                    except Exception as exc:
                        print(exc, flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def reply(self,code,body,kind):
        self.send_response(code)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css')}
        if self.path not in files:
            self.send_error(404)
            return
        name,kind=files[self.path]
        self.reply(200,(HERE.parent/"web"/name).read_bytes(),kind)

    def do_POST(self):
        if self.path!='/control' or not secrets.compare_digest(self.headers.get('X-Car-Key',''),self.server.bridge.token):
            self.send_error(403)
            return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=1024:
                raise ValueError('请求长度错误')
            result=self.server.bridge.run(json.loads(self.rfile.read(length)))
            code=200
        except (ValueError, AttributeError, OSError) as exc:
            code,result=409,{'error':str(exc)}
        self.reply(code,json.dumps(result).encode(),'application/json')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--bind',required=True)
    parser.add_argument('--serial',default='COM5')
    args=parser.parse_args()
    with ThreadingHTTPServer((args.bind,8765),Handler) as server, serial.Serial(args.serial,115200,timeout=.045,write_timeout=.2) as port:
        bridge=Bridge(port)
        bridge.command('AT+CAR=?',b'[CAR] VERSION car-stage1-v6')
        bridge.stop()
        server.bridge=bridge
        threading.Thread(target=bridge.watchdog,daemon=True).start()
        print(f'http://{args.bind}:8765/#{bridge.token}',flush=True)
        try:
            server.serve_forever()
        finally:
            bridge.stop()

