#!/usr/bin/env python3
"""管理朗读音色（~/.config/volc-tts/voices.json）。输出一行 JSON，给 mod 读。

用法：
  python3 voices.py list                 当前音色和全部音色
  python3 voices.py use <音色ID>          切换当前音色
  python3 voices.py remove <音色ID>       删除（至少保留一个）
  python3 voices.py add <音色ID> [名字]    验证后保存并切换为当前音色

验证就是真的合成一句试听语（约 10 个字，会计费）：先用 seed-tts-2.0，
音色和资源对不上时再试 seed-icl-2.0（声音复刻音色）；都不行就判定不存在或未开通。
合成出的音频写进缓存，所以紧接着的试听不再调用火山。
"""
import json
import os
import re
import sys

import volc


def reply(**kw):
    if not kw.get('ok') or kw.get('added'):  # 失败和添加记进朗读日志；list / use 太频繁，不记
        volc.log_to_file(f"voices.py {' '.join(sys.argv[1:])} → {'成功' if kw.get('ok') else '失败：' + kw.get('error', '')}")
    print(json.dumps(kw, ensure_ascii=False))


def default_name(voice_id):
    """没填名字时，从 ID 里取出中间那段，例如 ICL_uranus_zh_female_xingganmeihuo_tob → xingganmeihuo。"""
    parts = [p for p in voice_id.split('_')
             if p not in ('ICL', 'uranus', 'bigtts', 'tob', 'zh', 'en', 'female', 'male', 'saturn', 'mars', 'moon')]
    return parts[-1] if parts else voice_id


def validate(voice):
    """依次用各个资源合成试听语；成功返回资源名并写缓存，失败抛 VolcError。"""
    key = volc.read_key()
    text = volc.sample_text(voice)
    last = None
    for resource in volc.RESOURCES:
        audio = bytearray()
        try:
            volc.fetch(text, voice['id'], resource, key, audio.extend)
        except volc.VolcError as e:
            if e.code == volc.MISMATCH:
                last = e
                continue
            raise
        if not audio:
            raise volc.VolcError(None, '没有返回音频')
        os.makedirs(volc.CACHE_DIR, exist_ok=True)
        path = volc.cache_path(voice['id'], text)
        with open(f'{path}.{os.getpid()}.part', 'wb') as f:
            f.write(audio)
        os.replace(f'{path}.{os.getpid()}.part', path)
        return resource
    raise last


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'list'
    data = volc.load_voices()

    if action == 'list':
        return reply(ok=True, **data)

    voice_id = sys.argv[2].strip() if len(sys.argv) > 2 else ''
    if not re.fullmatch(r'[A-Za-z0-9_\-]{3,128}', voice_id):
        return reply(ok=False, error='音色 ID 只能由字母、数字、下划线和连字符组成', **data)

    if action == 'use':
        if not volc.find_voice(data, voice_id):
            return reply(ok=False, error='列表里没有这个音色', **data)
        data['current'] = voice_id
        volc.save_voices(data)
        return reply(ok=True, **data)

    if action == 'remove':
        if len(data['voices']) <= 1:
            return reply(ok=False, error='至少要保留一个音色', **data)
        data['voices'] = [v for v in data['voices'] if v['id'] != voice_id]
        if data['current'] == voice_id:
            data['current'] = data['voices'][0]['id']
        volc.save_voices(data)
        return reply(ok=True, **data)

    if action == 'add':
        name = ' '.join(sys.argv[3:]).strip() or default_name(voice_id)
        voice = {'id': voice_id, 'name': name[:40]}
        try:
            voice['resource'] = validate(voice)
        except volc.VolcError as e:
            hint = '音色不存在，或你的火山账号没有开通它' if e.code == volc.MISMATCH else str(e)
            return reply(ok=False, error=hint, **data)
        except OSError as e:
            return reply(ok=False, error=f'网络或文件错误：{e}', **data)
        data['voices'] = [v for v in data['voices'] if v['id'] != voice_id] + [voice]
        data['current'] = voice_id
        volc.save_voices(data)
        return reply(ok=True, added=voice, sample=volc.sample_text(voice), **data)

    reply(ok=False, error=f'不认识的操作：{action}', **data)


if __name__ == '__main__':
    main()
