"""stream.py 和 voices.py 共用的部分：火山接口、缓存路径、音色列表。只用标准库。"""
import base64
import codecs
import hashlib
import json
import os
import time
import urllib.request
import uuid

ENDPOINT = 'https://openspeech.bytedance.com/api/v3/tts/unidirectional'
RESOURCES = ('seed-tts-2.0', 'seed-icl-2.0')  # 官方 2.0 音色 / 声音复刻 2.0 音色
RATE = 24000
RUN_DIR = os.path.expanduser('~/.config/volc-tts')
KEY_FILE = os.path.join(RUN_DIR, 'api_key')
VOICES_FILE = os.path.join(RUN_DIR, 'voices.json')
CACHE_DIR = os.path.expanduser('~/.cache/volc-tts')
LOG_FILE = os.path.join(CACHE_DIR, 'stream.log')  # 朗读过程日志，排查用
LOG_MAX_BYTES = 1024 * 1024  # 超过就把旧的挪成 stream.log.1，只留两份
DEFAULT_VOICE = {'id': 'zh_female_zhixingnv_uranus_bigtts', 'name': '知性女声 2.0', 'resource': 'seed-tts-2.0'}
MISMATCH = 55000000  # 音色不存在，或音色和资源对不上


class VolcError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f'火山返回错误 {code}: {message}')
        self.code = code


def read_key():
    with open(KEY_FILE) as f:
        return f.read().strip()


def cache_path(speaker, text):
    return os.path.join(CACHE_DIR, hashlib.sha256(f'{speaker}\n{text}'.encode()).hexdigest() + '.pcm')


def log_to_file(line):
    """追加一行到 LOG_FILE；写不进去也不影响朗读。"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + '.1')
        with open(LOG_FILE, 'a') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {line}\n")
    except OSError:
        pass


def iter_objects(resp):
    """服务端返回首尾相接的 JSON 对象（不一定有换行），边读边切出完整对象。

    网络分块会切在汉字的字节中间（UTF-8 一个汉字 3 字节），所以用增量解码器：
    半个字先留着，等下一块到了再拼完整。
    """
    dec = json.JSONDecoder()
    text = codecs.getincrementaldecoder('utf-8')()
    buf = ''
    while True:
        data = resp.read1(65536)
        if not data:
            buf += text.decode(b'', final=True)  # 结尾还剩半个字就会在这里报错，说明数据真的不完整
            break
        buf += text.decode(data)
        while True:
            buf = buf.lstrip()
            if not buf:
                break
            try:
                obj, end = dec.raw_decode(buf)
            except json.JSONDecodeError:
                break  # 对象还没收全
            yield obj
            buf = buf[end:]


def fetch(text, speaker, resource, key, out, section_id=None):
    """流式合成 text，每收到一块 PCM 就调用 out(bytes)。出错抛 VolcError。

    section_id：同一个 ID 的多次请求按顺序发出时，服务端会记住前面合成过的内容，
    让后面的语气接得上（仅 2.0 音色和声音复刻 2.0 音色支持）。
    """
    additions = {'disable_markdown_filter': True, 'disable_emoji_filter': True}
    if section_id:
        additions['section_id'] = section_id
    req = urllib.request.Request(ENDPOINT, method='POST', headers={
        'X-Api-Key': key,
        'X-Api-Resource-Id': resource,
        'X-Api-Request-Id': str(uuid.uuid4()),
        'Content-Type': 'application/json',
    }, data=json.dumps({'req_params': {
        'text': text,
        'speaker': speaker,
        'audio_params': {'format': 'pcm', 'sample_rate': RATE},
        'additions': json.dumps(additions),
    }}).encode())
    with urllib.request.urlopen(req, timeout=30) as resp:
        for obj in iter_objects(resp):
            if obj.get('code') not in (0, 20000000):
                raise VolcError(obj.get('code'), obj.get('message'))
            if obj.get('data'):
                out(base64.b64decode(obj['data']))


def load_voices():
    """{"current": id, "voices": [{id, name, resource}]}；文件不存在时给出只有默认音色的列表。"""
    try:
        with open(VOICES_FILE) as f:
            data = json.load(f)
        if data.get('voices'):
            if data.get('current') not in [v['id'] for v in data['voices']]:
                data['current'] = data['voices'][0]['id']
            return data
    except (OSError, ValueError):
        pass
    return {'current': DEFAULT_VOICE['id'], 'voices': [dict(DEFAULT_VOICE)]}


def save_voices(data):
    tmp = f'{VOICES_FILE}.{os.getpid()}.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, VOICES_FILE)


def find_voice(data, voice_id):
    return next((v for v in data['voices'] if v['id'] == voice_id), None)


def current_voice():
    data = load_voices()
    return find_voice(data, data['current'])


def sample_text(voice):
    """试听用的那句话；验证时合成的也是它，所以验证后试听直接走缓存。"""
    return f"你好，我是{voice['name']}。"
