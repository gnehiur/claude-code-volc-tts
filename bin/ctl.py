#!/usr/bin/env python3
"""遥控正在进行的朗读（stream.py 启动的 mpv）。

用法：python3 ctl.py pause | resume | stop | next | prev | speed <倍数>
next / prev：下一节 / 上一节（本节念了 3 秒以上时，prev 回到本节开头）。
没有正在进行的朗读时，pause / resume 什么也不做；speed 只记下倍速，下次朗读生效。
"""
import json
import os
import signal
import socket
import subprocess
import sys

RUN_DIR = os.path.expanduser('~/.config/volc-tts')
PID_FILE = os.path.join(RUN_DIR, 'playing.pid')
SOCK = os.path.join(RUN_DIR, 'mpv.sock')
CTL_SOCK = os.path.join(RUN_DIR, 'stream.sock')  # stream.py 自己的遥控口，管跳节
SPEED_FILE = os.path.join(RUN_DIR, 'speed')


def send(command):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(SOCK)
            s.sendall((json.dumps({'command': command}) + '\n').encode())
            s.recv(4096)  # 等 mpv 回一句再断开，确保命令已执行
    except OSError:
        pass


def jump(direction):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(CTL_SOCK)
            s.sendall(f'{direction}\n'.encode())
            s.recv(16)  # 等 stream.py 换好播放器再返回
    except OSError:
        pass


def stop():
    """先核对进程身份，确认是朗读脚本才发 SIGTERM。"""
    try:
        pid = int(open(PID_FILE).read().strip())
    except (OSError, ValueError):
        return
    cmd = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
                         capture_output=True, text=True).stdout
    if 'stream.py' in cmd:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    if action == 'pause':
        send(['set_property', 'pause', True])
    elif action == 'resume':
        send(['set_property', 'pause', False])
    elif action == 'stop':
        stop()
    elif action in ('next', 'prev'):
        jump(action)
    elif action == 'speed' and len(sys.argv) > 2:
        speed = float(sys.argv[2])
        with open(SPEED_FILE, 'w') as f:
            f.write(f'{speed:g}')
        send(['set_property', 'speed', speed])
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
