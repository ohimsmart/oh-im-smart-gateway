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


# what the client uses to pick, start and reach the server (the rest is timeouts and descriptions)
_REACHES_SERVER = ('command', 'args', 'env', 'url', 'transport', 'headers', 'API_ACCESS_TOKEN')


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
    # A blank env value, header or token stands for the saved one. They go back only on a save that
    # changes nothing that reaches the server: the same keys, every value blank and the rest exactly as
    # saved. Any other save that leaves a saved secret blank is refused and names it (filling it in
    # could hand it to another host or program, dropping it could let the gateway's own variable of
    # that name through). Typed values are never compared with saved ones, so a save cannot confirm one.
    for field in ('env', 'headers'):
        values = result.get(field, {})
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise UIError("{field} 必须是字符串映射表", field=field)
    saved = {f: old.get(f) if isinstance(old.get(f), dict) else {} for f in ('env', 'headers')}
    filled = {f: {k: v or saved[f].get(k, '') for k, v in result[f].items()} for f in saved if f in result}
    if result.get('API_ACCESS_TOKEN') == '' and 'API_ACCESS_TOKEN' in old:
        filled['API_ACCESS_TOKEN'] = old['API_ACCESS_TOKEN']
    missing = object()
    untouched = (all(v == '' for f in filled if f in saved for v in result[f].values())
                 # (the same names in the same order: a server may read the first of two spellings)
                 and all(list(result.get(f, {})) == list(saved[f]) and (f in result) == (f in old) for f in saved)
                 and result.get('API_ACCESS_TOKEN', missing) == ('' if 'API_ACCESS_TOKEN' in old else missing)
                 and all(result.get(k, missing) == old.get(k, missing)
                         for k in _REACHES_SERVER if k not in ('env', 'headers', 'API_ACCESS_TOKEN')))
    if untouched:
        result.update(filled)
    else:   # (a blank saved as blank is no secret, so it stays as it is)
        lost = [k for f in filled if f in saved for k, v in result[f].items() if not v and saved[f].get(k)]
        if result.get('API_ACCESS_TOKEN') == '' and old.get('API_ACCESS_TOKEN'):
            lost.append('API_ACCESS_TOKEN')
        if lost:
            raise UIError("设置改了之后，这些已保存的值不会带过去，请重新填写或删掉：{keys}", keys=', '.join(lost))
    for field in ('timeout', 'sse_read_timeout'):
        if field in result:
            value = result[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 3600:
                raise UIError("{field} 必须在 0 到 3600 秒之间", field=field)
    if 'terminate_on_close' in result and not isinstance(result['terminate_on_close'], bool):
        raise UIError("terminate_on_close 必须是布尔值")
    return result


def save(path, name, config=None, delete=False, create=False):
    """create: the admin chose "Add server", so an existing server of that name is not replaced."""
    if not isinstance(name, str) or not re.fullmatch(r'[\w .-]{1,80}', name) or not name.strip():
        raise UIError("请填写服务器名称（最多 80 个字符）")
    path = Path(path)
    with _WRITE_LOCK:
        data = load(path)
        servers = data.setdefault('mcpServers', {})
        if delete:
            servers.pop(name, None)
        elif create and name in servers:
            raise UIError("已有同名的 MCP 服务器，请换一个名称，或从列表选它来修改")
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
