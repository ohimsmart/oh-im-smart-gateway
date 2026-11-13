"""插件设置：后台「意图识别」页插件旁边的「设置」。

字段清单在 catalog.PLUGIN_SETTINGS（按 id），PLUGINS 条目用 "settings" 指向它，值存在 plugins.<config> 下。
这里只读写配置文件；保存后同步到运行中的服务和在线连接由 AdminHandler 负责。
要加「测试」按钮：在 TESTS 里登记 async 函数 (values) -> dict，失败时抛 ValueError（message 给用户看）。
"""

from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from core.admin import catalog, config_store
from core.admin.i18n_msg import KeyedError, UIError, label, msg, render


def _spec(settings_id: str) -> dict:
    spec = catalog.PLUGIN_SETTINGS.get(settings_id)
    if not spec:
        raise KeyError(settings_id)
    return spec


def config_key(settings_id: str) -> str:
    """设置存在 plugins 下的哪个键（几个插件可以共用一份，例如智能家居的三个）。"""
    _spec(settings_id)
    return next(p["config"] for p in catalog.PLUGINS if p.get("settings") == settings_id)


def _saved(merged: dict, settings_id: str) -> dict:
    cfg = (merged.get("plugins") or {}).get(config_key(settings_id))
    return cfg if isinstance(cfg, dict) else {}


def _filled(value) -> bool:
    """上游的占位值（"你的…"、"mk-xxx"）算没填。"""
    return not config_store.is_placeholder(value) and not str(value).strip().lower().endswith("-xxx")


def _legacy(saved: dict, settings_id: str) -> bool:
    """配置里还留着旧版插件的字段（legacy_keys，例如联网搜索旧版的 provider）：
    那时填的 Key 是给别的服务的，新版不能用，也不能发出去，当成没填。"""
    return any(k in saved for k in _spec(settings_id).get("legacy_keys", ()))


def _secret_of(saved: dict, key: str, legacy: bool) -> str:
    """已保存的密钥（占位值、旧版留下的都算没有）。"""
    value = saved.get(key)
    return str(value).strip() if _filled(value) and not legacy else ""


def configured(merged: dict, settings_id: str) -> bool:
    saved = _saved(merged, settings_id)
    legacy = _legacy(saved, settings_id)
    return all(_secret_of(saved, f["key"], legacy) if f["type"] == "secret" else _filled(saved.get(f["key"]))
               for f in _spec(settings_id)["fields"] if f.get("required"))


def describe(merged: dict, settings_id: str) -> dict:
    spec, saved = _spec(settings_id), _saved(merged, settings_id)
    legacy = _legacy(saved, settings_id)
    fields = []
    for f in spec["fields"]:
        item = {k: v for k, v in f.items()}
        raw = saved.get(f["key"])
        if f["type"] == "secret":
            item["set"] = bool(_secret_of(saved, f["key"], legacy))
            item.update(config_store.masked_fields("hint_set", raw) if item["set"] else {"hint_set": ""})
        else:
            item["value"] = "" if raw is None or not _filled(raw) else raw
        fields.append(item)
    return {"id": settings_id, "note": spec.get("note", ""), "fields": fields,
            "legacy_note": spec.get("legacy_note", "") if legacy else "",
            "configured": configured(merged, settings_id), "testable": settings_id in TESTS}


def _effective(f: dict, value):
    """字段实际生效的值：没填（或占位值）用默认值。"""
    if value is None or str(value).strip() == "" or not _filled(value):
        return f.get("default", "")
    return value


def _values(settings_id: str, payload: dict, merged: dict) -> dict:
    """校验页面提交的值，返回完整的设置：secret 留空用已保存的，其他留空表示用默认值（存成空），
    页面没提交的字段用已保存的。"""
    payload = payload or {}
    saved = _saved(merged, settings_id)
    legacy = _legacy(saved, settings_id)
    values = {}
    for f in _spec(settings_id)["fields"]:
        key, field_label = f["key"], f["label"]
        if key not in payload:
            if f["type"] == "secret":
                values[key] = _secret_of(saved, key, legacy)
            else:
                values[key] = saved.get(key) if _filled(saved.get(key)) else ""
            continue
        raw = payload.get(key)
        text = "" if raw is None else str(raw).strip()
        if f["type"] == "secret":
            if any(c.isspace() for c in text):
                raise UIError("{label} 里不能有空格或换行", label=label(field_label))
            values[key] = text or _secret_of(saved, key, legacy)
        elif f["type"] == "number":
            if text == "":
                values[key] = ""
                continue
            try:
                number = int(text)
            except ValueError:
                raise UIError("{label} 要填整数", label=label(field_label))
            if ("min" in f and number < f["min"]) or ("max" in f and number > f["max"]):
                raise UIError("{label} 要在 {min} 到 {max} 之间", label=label(field_label), min=f.get("min"), max=f.get("max"))
            values[key] = number
        else:
            if "\n" in text:
                raise UIError("{label} 不能换行", label=label(field_label))
            if key.endswith("url") and text and not text.startswith(("http://", "https://")):
                raise UIError("{label} 要以 http:// 或 https:// 开头", label=label(field_label))
            values[key] = text
    return values


def save(settings_id: str, payload: dict, merged: dict, path: Path = None) -> list:
    """保存设置，返回修改的字段名。"""
    values = _values(settings_id, payload, merged)
    saved = _saved(merged, settings_id)
    legacy = _legacy(saved, settings_id)
    fields = {f["key"]: f for f in _spec(settings_id)["fields"]}
    # 旧版留下的密钥按"没填"比较：保存时一定写掉，不会留下来被新版当成有效的
    old = {k: (_secret_of(saved, k, legacy) if fields[k]["type"] == "secret" else saved.get(k)) for k in fields}
    changes = {k: v for k, v in values.items()
               if str(_effective(fields[k], v)).strip() != str(_effective(fields[k], old[k])).strip()
               or (legacy and fields[k]["type"] == "secret" and saved.get(k) is not None)}
    drop = [k for k in _spec(settings_id).get("legacy_keys", ()) if k in saved]
    if not changes and not drop:
        return []
    with config_store.edit_custom(path) as data:
        target = data.setdefault("plugins", CommentedMap()).setdefault(config_key(settings_id), CommentedMap())
        for key, value in changes.items():
            target[key] = value
        for key in drop:
            target.pop(key, None)
    return list(changes) + drop


def test_values(settings_id: str, payload: dict, merged: dict) -> dict:
    """测试用的设置：页面上还没保存的值优先。"""
    return _values(settings_id, payload, merged)


# ---------- 测试函数 ----------
# settings_id -> async (values) -> dict；values 是 test_values 整理好的完整设置，失败时抛 ValueError

async def _test_web_search(values: dict) -> dict:
    from plugins_func.functions import web_search

    settings = web_search.settings_of({"plugins": {"web_search": values}})
    try:
        result = await web_search.search(settings, "今天的重要新闻")
    except web_search.SearchError as e:
        raise UIError.from_msg(e.payload()) from e
    if not result.results:
        raise ValueError("接口执行了搜索，但没有返回结果")
    return {"seconds": result.seconds, "count": len(result.results), "first": result.results[0]["title"]}


async def _test_weather(values: dict) -> dict:
    """用页面上填的所在地查一次（没填就是 IP 判断的城市），看地名认得对不对。"""
    from types import SimpleNamespace

    from plugins_func.functions import get_weather

    conn = SimpleNamespace(config={"plugins": {"get_weather": values}}, client_ip="127.0.0.1")
    try:
        report = await get_weather.local_report(conn)
    except get_weather.WeatherError as e:
        raise UIError.from_msg(e.payload()) from e
    lines = report.splitlines()
    place = lines[0].removeprefix("地点：")
    # 第二行是设备朗读的天气播报，跟对话内容同类：原样显示
    m = msg("查到的地点是 {place}。{weather}", place=place, weather=lines[1] if len(lines) > 1 else "")
    return {"place": place, "message": render(m), "message_msg": m}


TESTS = {"web_search": _test_web_search, "get_weather": _test_weather}


def _redact(message: str, values: dict, settings_id: str) -> str:
    """报错信息里不带出密钥（有的接口会把收到的 Key 原样写进错误信息）。"""
    for f in _spec(settings_id)["fields"]:
        secret = str(values.get(f["key"]) or "") if f["type"] == "secret" else ""
        if len(secret) >= 4:
            message = message.replace(secret, "***")
    return message


def _redact_params(params: dict, values: dict, settings_id: str) -> dict:
    """params（msg_params 或结果字典）里的普通字符串做 _redact()，msg 物件递归。"""
    def one(v):
        if isinstance(v, dict) and "msg_key" in v:
            return {"msg_key": v["msg_key"], "msg_params": _redact_params(v.get("msg_params") or {}, values, settings_id)}
        return _redact(v, values, settings_id) if isinstance(v, str) else v
    return {k: one(v) for k, v in params.items()}


async def run_test(settings_id: str, payload: dict, merged: dict) -> dict:
    if settings_id not in TESTS:
        raise KeyError(settings_id)
    values = test_values(settings_id, payload, merged)
    try:
        result = await TESTS[settings_id](values)
    except ValueError as e:
        if isinstance(e, KeyedError):
            # 字典直接写出来：键的字面值在原本抛出它的地方
            raise UIError.from_msg({"msg_key": e.key, "msg_params": _redact_params(e.params, values, settings_id)}) from e
        raise ValueError(_redact(str(e), values, settings_id))
    return _redact_params(result, values, settings_id)   # 字符串字段和 message_msg 的参数都要遮
