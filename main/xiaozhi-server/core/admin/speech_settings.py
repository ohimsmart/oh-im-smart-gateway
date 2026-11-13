"""OpenAI-compatible speech settings; credentials remain write-only."""
import math
import re
import hashlib
import json
from urllib.parse import urlsplit

from ruamel.yaml.comments import CommentedMap

from core.admin import config_store
from core.admin.i18n_msg import UIError
from core.agent import reply_language

SPEECH = {
    'ASR': {'entry': 'OpenAIASR', 'endpoint': '/audio/transcriptions', 'model_field': 'model_name',
            'url_field': 'base_url', 'model': 'whisper-1'},
    'TTS': {'entry': 'OpenAITTS', 'endpoint': '/audio/speech', 'model_field': 'model',
            'url_field': 'api_url', 'model': 'tts-1'},
}


def tts_profile(config):
    """Public voice identity for device audio caches; never include credentials."""
    name = (config.get('selected_module') or {}).get('TTS')
    entry = (config.get('TTS') or {}).get(name) or {}
    fields = ('type', 'model', 'voice', 'private_voice', 'speed', 'speech_rate', 'volume',
              'pitch_rate', 'format', 'pcm_sample_rate', 'api_protocol', 'api_url', 'base_url',
              'ttsRate', 'ttsVolume', 'ttsPitch')
    language = config.get('reply_language') or ''
    identity = {'entry': name, 'language': language, **{k: entry.get(k) for k in fields}}
    revision = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
    return {'revision': revision, 'entry': name, 'type': entry.get('type'), 'model': entry.get('model'),
            'voice': entry.get('private_voice') or entry.get('voice'), 'speed': entry.get('speed'),
            'language': language, 'wake_text': reply_language.phrase('wake', language)}


def describe(section, merged):
    spec = SPEECH[section]
    entry = (merged.get(section) or {}).get(spec['entry']) or {}
    endpoint = str(entry.get(spec['url_field']) or 'https://api.openai.com/v1' + spec['endpoint'])
    key = entry.get('api_key')
    result = {'entry': spec['entry'], 'base_url': endpoint.removesuffix(spec['endpoint']),
              'model': entry.get(spec['model_field']) or spec['model'],
              'key_set': not config_store.is_placeholder(key), **config_store.masked_fields('key_hint', key),
              'conflict': bool(entry) and entry.get('type') != 'openai'}
    result['api_protocol'] = entry.get('api_protocol') or 'openai'
    result['configured'] = result['key_set'] and not result['conflict']
    if section == 'ASR':
        result.update(language=entry.get('language') or '', prompt=entry.get('prompt') or '')
    else:
        result.update(voice=entry.get('private_voice') or entry.get('voice') or 'alloy',
                      speed=entry.get('speed') or 1, format=entry.get('format') or 'wav',
                      pcm_sample_rate=entry.get('pcm_sample_rate') or 24000)
    return result


def save(section, payload, merged, path=None):
    spec = SPEECH[section]
    old = (merged.get(section) or {}).get(spec['entry']) or {}
    if old and old.get('type') != 'openai':
        raise UIError("{entry} 已存在，但不是 OpenAI 兼容类型", entry=spec['entry'])
    defaults = describe(section, merged)
    protocol = payload.get('api_protocol', defaults['api_protocol'])
    if protocol not in ('openai', 'zenmux'):
        raise UIError("请选择 OpenAI 或 ZenMux 语音协议")
    base = str(payload.get('base_url', defaults['base_url'])).strip().rstrip('/')
    url = urlsplit(base)
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(c.isspace() for c in base)):
        raise UIError("Base URL 必须是完整的 HTTP(S) 网址，不能含账号密码、查询参数或空格")
    model = str(payload.get('model', defaults['model'])).strip()
    if not model or len(model) > 200:
        raise UIError("请填写模型名称（最多 200 个字符）")
    key = str(payload.get('api_key') or '').strip()
    if any(c.isspace() for c in key):
        raise UIError("API Key 不能含空格或换行")
    if not key and config_store.is_placeholder(old.get('api_key')):
        raise UIError("请填写 API Key")
    values = {'type': 'openai', 'api_protocol': protocol,
              spec['url_field']: base + spec['endpoint'], spec['model_field']: model}
    if key:
        values['api_key'] = key
    if not old:
        values['output_dir'] = 'tmp/'
    if section == 'ASR':
        language = str(payload.get('language', defaults['language'])).strip()
        if language and not re.fullmatch(r'[a-z]{2,3}', language):
            raise UIError("识别语言请使用 ISO 639 代码，例如 zh 或 en；留空则自动检测")
        prompt = str(payload.get('prompt', defaults['prompt'])).strip()
        if len(prompt) > 4000:
            raise UIError("转写提示最多 4000 个字符")
        values.update(language=language, prompt=prompt)
    else:
        voice = str(payload.get('voice', defaults['voice'])).strip()
        try:
            speed = float(payload.get('speed', defaults['speed']))
        except (TypeError, ValueError):
            raise UIError("语速必须在 0.25 到 4 之间")
        if not voice or len(voice) > 200 or not math.isfinite(speed) or not .25 <= speed <= 4:
            raise UIError("请填写音色，语速必须在 0.25 到 4 之间")
        values.update(voice=voice, speed=speed)
        audio_format = payload.get('format', defaults['format'])
        if audio_format not in ('wav', 'mp3', 'pcm', 'opus', 'aac', 'flac'):
            raise UIError("不支持的音频格式")
        rate = payload.get('pcm_sample_rate', defaults['pcm_sample_rate'])
        if str(rate) not in ('8000', '16000', '22050', '24000', '32000', '44100', '48000'):
            raise UIError("请选择有效的 PCM 采样率")
        values.update(format=audio_format, pcm_sample_rate=int(rate))
        if old.get('private_voice'):
            values['private_voice'] = ''
    changes = {k: v for k, v in values.items() if old.get(k) != v}
    if changes:
        with config_store.edit_custom(path) as data:
            data.setdefault(section, CommentedMap()).setdefault(spec['entry'], CommentedMap()).update(changes)
    return list(changes)
