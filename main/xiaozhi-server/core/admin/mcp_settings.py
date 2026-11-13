"""Atomic MCP configuration editing; never return environment or header secrets."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from core.admin.config_store import _WRITE_LOCK
from core.admin.i18n_msg import UIError


def load(path):
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {'mcpServers': {}}
    if not isinstance(data, dict) or not isinstance(data.get('mcpServers', {}), dict):
        raise UIError("MCP 设置必须包含 mcpServers 对象")
    if not all(isinstance(config, dict) for config in data.get('mcpServers', {}).values()):
        raise UIError("每个 MCP 服务器设置都必须是 JSON 对象")
    return data


def describe(path):
    servers = {}
    for name, config in load(path).get('mcpServers', {}).items():
        visible = {k: v for k, v in config.items() if k not in ('env', 'headers', 'API_ACCESS_TOKEN')}
        for field in ('env', 'headers'):
            if field in config:
                visible[field] = {k: '' for k in config[field]}
        if 'API_ACCESS_TOKEN' in config:
            visible['API_ACCESS_TOKEN'] = ''
        servers[name] = visible
    return {'servers': servers, 'note': '儲存後，裝置重新連線時生效。API 模型需啟用工具呼叫；Claude Code 使用自己的 MCP 設定。'}


def validate(config, old):
    if not isinstance(config, dict):
        raise UIError("服务器设置必须是 JSON 对象")
    allowed = {'command', 'args', 'env', 'url', 'transport', 'headers', 'API_ACCESS_TOKEN',
               'timeout', 'sse_read_timeout', 'terminate_on_close', 'des', 'link'}
    if set(config) - allowed:
        raise UIError("包含不支持的 MCP 设置字段")
    result = dict(config)
    if bool(result.get('command')) == bool(result.get('url')):
        raise UIError("请设置 command 或 url，二选一")
    if result.get('command'):
        if not isinstance(result['command'], str) or not result['command'].strip():
            raise UIError("command 必须是非空字符串")
        if not isinstance(result.get('args', []), list) or not all(isinstance(a, str) for a in result.get('args', [])):
            raise UIError("args 必须是字符串数组")
    else:
        result.pop('command', None)  # SDK selects stdio by key presence, including an empty string.
        if not isinstance(result['url'], str):
            raise UIError("url 必须是字符串")
        url = urlsplit(result['url'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise UIError("请使用 HTTP(S) 网址；令牌请放在 headers，不要放在网址里")
        if result.get('transport', 'sse') not in ('sse', 'http', 'streamable-http'):
            raise UIError("传输类型必须是 sse 或 streamable-http")
    for field in ('env', 'headers'):
        if field not in result:
            continue
        values = result[field]
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise UIError("{field} 必须是字符串映射表", field=field)
        result[field] = {k: (v if v else (old.get(field) or {}).get(k, '')) for k, v in values.items()}
    if result.get('API_ACCESS_TOKEN') == '':
        result['API_ACCESS_TOKEN'] = old.get('API_ACCESS_TOKEN', '')
    for field in ('timeout', 'sse_read_timeout'):
        if field in result:
            value = result[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 3600:
                raise UIError("{field} 必须在 0 到 3600 秒之间", field=field)
    if 'terminate_on_close' in result and not isinstance(result['terminate_on_close'], bool):
        raise UIError("terminate_on_close 必须是布尔值")
    return result


def save(path, name, config=None, delete=False):
    if not isinstance(name, str) or not re.fullmatch(r'[\w .-]{1,80}', name) or not name.strip():
        raise UIError("请填写服务器名称（最多 80 个字符）")
    path = Path(path)
    with _WRITE_LOCK:
        data = load(path)
        servers = data.setdefault('mcpServers', {})
        if delete:
            servers.pop(name, None)
        else:
            servers[name] = validate(config, servers.get(name) or {})
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.mcp.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
