#!/usr/bin/env python3
"""从 stdin 读要念的文字，按“节”流式调用火山引擎豆包语音，收到一块 PCM 就写进 mpv 播放。

用法：echo "你好" | python3 stream.py [音色ID]
不给音色 ID 时用 ~/.config/volc-tts/voices.json 里的当前音色（由 voices.py 管理）。
只用标准库；播放器是 Homebrew 的 mpv。被 SIGTERM/SIGINT 时连同 mpv 一起退出（= 停止朗读）。

节：快进 / 后退的单位。边界是 Markdown 标题（## …）、分隔线（---）、单独一行的粗体（**这次攒下了什么**）；
一节都没有切出来时按段落切，太短的段落并进下一段。每节单独合成、单独缓存。

规则：
- 全局只有一个声音：开始前让上一个还在念的 stream.py 停下（pid 记在 PID_FILE）。
- 切走就暂停：盯着桌面 App 主日志，一出现会话切换（setFocusedSession）就让 mpv 暂停。
- 暂停 / 继续 / 倍速：mpv 开着遥控口 SOCK；上一节 / 下一节：本脚本开着遥控口 CTL_SOCK。都由 ctl.py 发命令。
- 跳节的做法：停掉当前 mpv，从目标节的音频重新开一个（mpv 读的是管道，不能往前跳）。
- 暂停超过 PAUSE_LIMIT 秒自动结束。
- 同一节文字念过一次后缓存在 CACHE_DIR，再念不调用火山。

stdout 协议（给 mod 读，一行一条）：
  HB              心跳，每 0.2 秒一次
  STATE playing   正在念
  STATE paused    已暂停
  SPEED 1.25      当前倍速
  SECTION 2/5     正在念第几节 / 共几节
"""
import glob
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

import volc

CHUNK_CHARS = 800  # 单次请求的最大字数（一节太长时拆成几次请求）
MIN_PARA_CHARS = 135  # 按段落切时，每节至少这么多字（1× 约 30 秒）
PREV_RESTART_SECONDS = 3  # ⏮：本节念了超过这么久就回本节开头，否则回上一节
PAUSE_LIMIT = 30 * 60  # 暂停超过这么久自动结束
PREFETCH = 2  # 正在念第 k 节时，提前下载到第 k+PREFETCH 节（火山流式接口大约按实时速度返回）
# 连贯：同一次朗读的所有请求带同一个 section_id、一次只发一个、按顺序发，服务端就会接着前面的语气念
RUN_DIR = volc.RUN_DIR
PID_FILE = os.path.join(RUN_DIR, 'playing.pid')
SOCK = os.path.join(RUN_DIR, 'mpv.sock')
CTL_SOCK = os.path.join(RUN_DIR, 'stream.sock')
SPEED_FILE = os.path.join(RUN_DIR, 'speed')
CACHE_DIR = volc.CACHE_DIR
CACHE_DAYS = 7
CACHE_MAX_BYTES = 200 * 1024 * 1024
RATE = volc.RATE
BYTES_PER_SECOND = RATE * 2  # s16le 单声道
APP_LOG = os.environ.get('VOLC_TTS_APP_LOG') or os.path.expanduser('~/Library/Logs/Claude/main.log')  # 环境变量仅供测试
FOCUS_MARK = 'LocalSessions.setFocusedSession:'
MPV = next((p for p in ('/opt/homebrew/bin/mpv', '/usr/local/bin/mpv') if os.path.exists(p)), 'mpv')

t0 = time.monotonic()
out_lock = threading.Lock()


def log(msg):
    line = f'[{time.monotonic() - t0:5.2f}s] {msg}'
    print(line, file=sys.stderr, flush=True)
    volc.log_to_file(line)


def emit(line):
    with out_lock:
        try:
            sys.stdout.write(line + '\n')
            sys.stdout.flush()
        except (BrokenPipeError, ValueError):
            pass


def read_speed():
    try:
        return float(open(SPEED_FILE).read().strip())
    except (OSError, ValueError):
        return 1.0


# ---------- 切节 ----------

HEADING = re.compile(r'^#{1,6}\s+\S')
RULE = re.compile(r'^\s*(?:-{3,}|\*{3,}|_{3,})\s*$')
BOLD_LINE = re.compile(r'^\s*\*\*[^*\n]+\*\*\s*[:：]?\s*$')
SKIPPED = re.compile(r'^\s*（这里有一(?:段代码|个表格)，略过）\s*$')  # mod 替换掉代码块、表格后留下的占位句


def split_sections(text):
    """按标题、分隔线、粗体行切节；切不出来就按段落切，太短的段落并进下一段。"""
    sections, cur = [], []

    def flush():
        body = '\n'.join(cur).strip()
        if body:
            sections.append(body)
        cur.clear()

    for line in text.split('\n'):
        if RULE.match(line):
            flush()
            continue  # 分隔线本身不念
        if HEADING.match(line) or BOLD_LINE.match(line):
            flush()
        cur.append(line)
    flush()

    # 只有标题、没有正文的节（例如 ## 后面紧跟着 ###，或正文只是被略过的代码 / 表格）并进下一节
    def heading_only(body):
        return all(not l.strip() or HEADING.match(l) or BOLD_LINE.match(l) or SKIPPED.match(l)
                   for l in body.split('\n'))

    merged = []
    for body in sections:
        if merged and heading_only(merged[-1]):
            merged[-1] += '\n' + body
        else:
            merged.append(body)
    sections = merged

    if len(sections) <= 1:
        sections, buf = [], ''
        for para in re.split(r'\n\s*\n', text.strip()):
            if not para.strip():
                continue
            buf = f'{buf}\n\n{para}' if buf else para
            if len(buf) >= MIN_PARA_CHARS:
                sections.append(buf)
                buf = ''
        if buf:
            if sections and len(buf) < MIN_PARA_CHARS:
                sections[-1] += '\n\n' + buf  # 结尾太短的并进上一节
            else:
                sections.append(buf)
    return sections or [text]


def split_requests(text):
    """一节太长时拆成几次请求，每次不超过 CHUNK_CHARS 字。"""
    parts, buf = [], ''
    for para in re.split(r'\n+', text):
        pieces = re.split(r'(?<=[。！？；.!?;])', para) if len(para) > CHUNK_CHARS else [para]
        for p in pieces:
            if buf and len(buf) + len(p) + 1 > CHUNK_CHARS:
                parts.append(buf)
                buf = ''
            buf = f'{buf}\n{p}' if buf else p
    if buf.strip():
        parts.append(buf)
    return parts


# ---------- 进程、缓存 ----------

def stop_previous():
    """让上一个还在念的 stream.py 停下：先核对进程身份，确认是朗读脚本才发 SIGTERM。"""
    try:
        pid = int(open(PID_FILE).read().strip())
    except (OSError, ValueError):
        pid = 0
    if pid and pid != os.getpid():
        cmd = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
                             capture_output=True, text=True).stdout
        if 'stream.py' in cmd:
            log(f'停止上一个朗读（pid {pid}）')
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            time.sleep(0.1)  # 等它的 mpv 退出、让出遥控口
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def release_pid():
    try:
        if open(PID_FILE).read().strip() == str(os.getpid()):
            os.remove(PID_FILE)
            os.remove(CTL_SOCK)
    except OSError:
        pass


def prune_cache():
    """删掉超过 CACHE_DAYS 天的缓存；总量超过 CACHE_MAX_BYTES 时从最旧的删起。"""
    for part in glob.glob(os.path.join(CACHE_DIR, '*.part')):  # 中途被停掉留下的半截下载
        try:
            if time.time() - os.stat(part).st_mtime > 3600:
                os.remove(part)
        except OSError:
            pass
    files = []
    for path in glob.glob(os.path.join(CACHE_DIR, '*.pcm')):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if time.time() - st.st_mtime > CACHE_DAYS * 86400:
            os.remove(path)
        else:
            files.append((st.st_mtime, st.st_size, path))
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= CACHE_MAX_BYTES:
            break
        os.remove(path)
        total -= size


class Section:
    def __init__(self, text):
        self.text = text
        self.audio = bytearray()
        self.started = False  # 下载是否已经开始
        self.done = False
        self.error = None

    def seconds(self):
        return len(self.audio) / BYTES_PER_SECOND


# ---------- mpv ----------

class Mpv:
    """一个 mpv 进程的遥控口连接：发命令，并把 pause / speed 的变化转成 stdout 协议，记下播放位置。"""

    def __init__(self, player):
        self.player = player
        self.sock = None
        self.lock = threading.Lock()
        self.paused_at = None
        self.time_pos = 0.0
        self.alive = True

    def connect(self):
        for _ in range(60):
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(SOCK)
                self.sock = s
                break
            except OSError:
                time.sleep(0.05)
        if not self.sock:
            log('连不上 mpv 遥控口，暂停 / 倍速 / 跳节不可用')
            return
        self.send(['observe_property', 1, 'pause'])
        self.send(['observe_property', 2, 'speed'])
        self.send(['observe_property', 3, 'time-pos'])
        threading.Thread(target=self.read_events, daemon=True).start()

    def send(self, command):
        if not self.sock:
            return
        with self.lock:
            try:
                self.sock.sendall((json.dumps({'command': command}) + '\n').encode())
            except OSError:
                pass

    def read_events(self):
        buf = b''
        while self.alive:
            try:
                data = self.sock.recv(4096)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get('event') != 'property-change' or not self.alive:
                    continue
                name, value = ev.get('name'), ev.get('data')
                if name == 'pause':
                    self.paused_at = time.monotonic() if value else None
                    emit('STATE paused' if value else 'STATE playing')
                elif name == 'speed' and value:
                    emit(f'SPEED {value:g}')
                elif name == 'time-pos' and value is not None:
                    self.time_pos = value


# ---------- 朗读 ----------

class Narration:
    def __init__(self, sections, speaker, resource):
        self.sections = sections
        self.speaker = speaker
        self.resource = resource
        self.cond = threading.Condition()  # 下载线程往 Section.audio 里添数据时通知播放线程
        self.lock = threading.RLock()  # 换 mpv 时用
        self.player = None
        self.mpv = None
        self.base = 0  # 当前 mpv 是从第几节开始播的
        self.generation = 0
        self.finished = threading.Event()
        self.errors = []
        self.reported = -1
        self.section_id = str(uuid.uuid4())
        self.want = (0, -1)  # 需要下载的节的范围（含两端），由播放位置决定
        threading.Thread(target=self.download_worker, daemon=True).start()

    # 下载：只有一个下载线程，一次合成一节，按需挑选（正在念的节和后面 PREFETCH 节里最靠前的那个）。
    # 一次只发一个请求、按顺序发，是 section_id 起作用的前提。命中缓存就直接读文件。
    def ensure(self, first, last=None):
        with self.cond:
            self.want = (first, last if last is not None else first)
            self.cond.notify_all()

    def download_worker(self):
        while True:
            with self.cond:
                while True:
                    first, last = self.want
                    todo = [i for i in range(first, min(len(self.sections), last + 1))
                            if not self.sections[i].started]
                    if todo:
                        i = todo[0]
                        self.sections[i].started = True
                        break
                    self.cond.wait(0.5)
            self.download(i)

    def download(self, i):
        sec = self.sections[i]
        path = volc.cache_path(self.speaker, sec.text)
        try:
            if os.path.exists(path):
                log(f'第 {i + 1} 节命中缓存')
                os.utime(path)
                with open(path, 'rb') as f:
                    data = f.read()
                with self.cond:
                    sec.audio += data
            else:
                key = volc.read_key()

                def out(block):
                    with self.cond:
                        sec.audio += block
                        self.cond.notify_all()

                for j, part in enumerate(split_requests(sec.text)):
                    log(f'请求第 {i + 1} 节第 {j + 1} 段（{len(part)} 字）')
                    volc.fetch(part, self.speaker, self.resource, key, out, self.section_id)
                tmp = f'{path}.{os.getpid()}.{i}.part'
                with open(tmp, 'wb') as f:
                    f.write(sec.audio)
                os.replace(tmp, path)  # 整节下载成功才算缓存
        except Exception as e:  # noqa: BLE001
            log(f'第 {i + 1} 节下载失败：{type(e).__name__}: {e}')
            with self.cond:
                sec.error = e
                self.errors.append(e)
        with self.cond:
            sec.done = True
            self.cond.notify_all()
            all_done = all(x.done for x in self.sections)
        if all_done:
            prune_cache()

    # 播放：从第 start 节开始，把各节音频依次写进这个 mpv
    def feed(self, start, player, generation):
        for k in range(start, len(self.sections)):
            sec = self.sections[k]
            self.ensure(k, k + PREFETCH)
            offset = 0
            while True:
                with self.cond:
                    while len(sec.audio) == offset and not sec.done and generation == self.generation:
                        self.cond.wait(0.2)
                    if generation != self.generation:
                        return
                    chunk = bytes(sec.audio[offset:offset + BYTES_PER_SECOND])
                    done = sec.done
                if chunk:
                    try:
                        player.stdin.write(chunk)
                        player.stdin.flush()
                    except (BrokenPipeError, ValueError):
                        return
                    offset += len(chunk)
                elif done:
                    break
            if sec.error:
                break  # 这一节下载失败：念完已收到的部分就结束，并报错
        try:
            player.stdin.close()
        except (BrokenPipeError, ValueError):
            pass

    def await_end(self, player, generation):
        player.wait()
        if generation == self.generation:  # 不是因为跳节被换掉的，而是自然播完
            self.finished.set()

    def play_from(self, start):
        with self.lock:
            self.generation += 1
            generation = self.generation
            if self.mpv:
                self.mpv.alive = False
            if self.player:
                self.player.kill()
                self.player.wait()
            with self.cond:
                self.cond.notify_all()  # 叫醒旧的写入线程，让它发现自己过期了
            try:
                os.remove(SOCK)
            except OSError:
                pass
            self.base = start
            self.player = subprocess.Popen(
                [MPV, '--no-video', '--really-quiet', '--no-terminal', '--cache=no',
                 f'--input-ipc-server={SOCK}', f'--speed={read_speed():g}',
                 # 输出成立体声：有的扬声器（如 Mac mini 内置）不接受单声道，CoreAudio 报 -50，
                 # mpv 退到 avfoundation 后会提前结束、声音念不完
                 '--audio-channels=stereo',
                 '--demuxer=rawaudio', '--demuxer-rawaudio-format=s16le',
                 f'--demuxer-rawaudio-rate={RATE}', '--demuxer-rawaudio-channels=1', '-'],
                stdin=subprocess.PIPE)
            self.mpv = Mpv(self.player)
            self.mpv.connect()
            self.reported = -1
            threading.Thread(target=self.feed, args=(start, self.player, generation), daemon=True).start()
            threading.Thread(target=self.await_end, args=(self.player, generation), daemon=True).start()

    def position(self):
        """现在念到第几节、这一节念了几秒。"""
        with self.lock:
            tp = self.mpv.time_pos if self.mpv else 0.0
            k, acc = self.base, 0.0
            with self.cond:
                while k < len(self.sections) - 1 and self.sections[k].done:
                    d = self.sections[k].seconds()
                    if tp < acc + d:
                        break
                    acc += d
                    k += 1
            return k, max(0.0, tp - acc)

    def jump(self, direction):
        k, into = self.position()
        if direction == 'next':
            target = k + 1
        else:
            target = k if into > PREV_RESTART_SECONDS else max(0, k - 1)
        if target >= len(self.sections):
            log('已是最后一节，下一节 = 结束')
            self.stop()
        log(f'{"下一节" if direction == "next" else "上一节"}：第 {k + 1} 节（已念 {into:.1f}s）→ 第 {target + 1} 节')
        self.play_from(target)

    def stop(self, *_):
        with self.lock:
            if self.player:
                self.player.kill()
        release_pid()
        os._exit(0)

    def pause(self):
        with self.lock:
            if self.mpv:
                self.mpv.send(['set_property', 'pause', True])

    # 背景线程们
    def report_section(self):
        n = len(self.sections)
        while True:
            k, _ = self.position()
            if k != self.reported:
                self.reported = k
                emit(f'SECTION {k + 1}/{n}')
            time.sleep(0.3)

    def watch_pause(self):
        while True:
            time.sleep(5)
            mpv = self.mpv
            if mpv and mpv.paused_at and time.monotonic() - mpv.paused_at > PAUSE_LIMIT:
                log('暂停太久，自动结束')
                self.stop()

    def serve_control(self):
        """本脚本的遥控口：ctl.py 发来 next / prev。"""
        try:
            os.remove(CTL_SOCK)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(CTL_SOCK)
        srv.listen(4)
        while True:
            conn, _ = srv.accept()
            with conn:
                try:
                    cmd = conn.recv(64).decode().strip()
                    if cmd in ('next', 'prev'):
                        self.jump(cmd)
                    conn.sendall(b'ok\n')
                except OSError:
                    pass


def watch_focus(on_switch):
    """从日志末尾开始读新增行；出现会话切换就回调。日志不存在（例如终端里用）就什么也不做。"""
    try:
        f = open(APP_LOG, 'rb')
    except OSError:
        return
    f.seek(0, os.SEEK_END)
    inode = os.fstat(f.fileno()).st_ino
    while True:
        line = f.readline()
        if line:
            if FOCUS_MARK.encode() in line:
                log('检测到切换会话，暂停')
                on_switch()
            continue
        time.sleep(0.1)
        try:  # 日志轮转（换了新文件或被截短）时重新打开
            st = os.stat(APP_LOG)
            if st.st_ino != inode or st.st_size < f.tell():
                f.close()
                f = open(APP_LOG, 'rb')
                inode = os.fstat(f.fileno()).st_ino
        except OSError:
            pass


def main():
    text = sys.stdin.read().strip()
    if not text:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    voices = volc.load_voices()
    if len(sys.argv) > 1:
        speaker = sys.argv[1]
        known = volc.find_voice(voices, speaker)
        resource = known['resource'] if known else volc.RESOURCES[0]
    else:
        voice = volc.find_voice(voices, voices['current'])
        speaker, resource = voice['id'], voice['resource']
    sections = [Section(s) for s in split_sections(text)]
    log(f'音色 {speaker}（{resource}）')
    log(f'开始朗读，{len(text)} 字，{len(sections)} 节：' + ' | '.join(s.text.split('\n')[0][:16] for s in sections))
    stop_previous()

    narration = Narration(sections, speaker, resource)
    signal.signal(signal.SIGTERM, narration.stop)
    signal.signal(signal.SIGINT, narration.stop)

    narration.play_from(0)  # 开始念第 1 节，同时预取后面两节
    for target in (narration.report_section, narration.watch_pause, narration.serve_control):
        threading.Thread(target=target, daemon=True).start()
    threading.Thread(target=watch_focus, args=(narration.pause,), daemon=True).start()

    # 心跳：让 mod 那边的读取循环每 0.2 秒醒一次；mod 关了管道就说明它不要我们了
    def heartbeat():
        while True:
            with out_lock:
                try:
                    sys.stdout.write('HB\n')
                    sys.stdout.flush()
                except (BrokenPipeError, ValueError):
                    narration.stop()
            time.sleep(0.2)

    threading.Thread(target=heartbeat, daemon=True).start()

    narration.finished.wait()
    release_pid()
    log('播放结束')
    if narration.errors:
        err = narration.errors[0]
        log(f'失败：{type(err).__name__}: {err}')
        print(f'火山朗读失败：{err}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
