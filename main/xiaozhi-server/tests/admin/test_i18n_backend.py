"""後端訊息：UIError 帶 key；錯誤回應帶 msg_key；英文介面顯示英文。"""
import ast
import json
import re
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import config_store, mcp_settings, speech_settings
from core.admin.admin_handler import COOKIE_NAME, AdminHandler
from core.admin.i18n_msg import UIError, msg, render
from core.providers.asr import doubao_stream   # 模組載入時讀設定（asyncio.run），不能在測試的事件循環裏 import
from tests.admin.i18n_node import KEY_CONSTRUCTORS, has_chinese, key_calls, python_sources
from tests.admin.i18n_node import render as render_js
from tests.admin.test_admin_pipeline import CLI_YAML


def test_render_fills_nested_parameters():
    assert render(msg("{why}，已退回 {old}", why=msg("缺少 {x}", x="a.onnx"), old="1.0")) == "缺少 a.onnx，已退回 1.0"
    assert render(msg("缺参数 {x}")) == "缺参数 {x}"
    e = UIError("{field} 必须是字符串映射表", field="env")
    assert isinstance(e, ValueError) and str(e) == "env 必须是字符串映射表"
    assert e.payload() == {"msg_key": "{field} 必须是字符串映射表", "msg_params": {"field": "env"}}


@pytest.mark.asyncio
async def test_validation_error_response_carries_the_key(tmp_path):
    path = tmp_path / ".config.yaml"; path.write_text("{}\n")
    h = AdminHandler(config_store.load_merged(custom_path=path), config_path=path)  # MCP file goes beside it
    app = web.Application(); h.register(app)
    async with TestClient(TestServer(app)) as c:
        c.session.cookie_jar.update_cookies({COOKIE_NAME: h.sessions.create()})
        r = await c.post("/admin/api/mcp", json={"name": "x", "config": {"url": "ftp://bad"}})
        assert r.status == 400
        d = await r.json()
        assert d["msg_key"] == "请使用 HTTP(S) 网址；令牌请放在 headers，不要放在网址里"
        assert d["msg_params"] == {}
        assert d["error"] == d["msg_key"]          # old pages still get a sentence


async def _use_missing_model(tmp_path):
    """切到配置里没有的模型（config.yaml 里没有 ClaudeCLI，它只在安装范本里）：返回错误响应的 JSON。"""
    path = tmp_path / ".config.yaml"; path.write_text("{}\n")
    h = AdminHandler(config_store.load_merged(custom_path=path), config_path=path)
    app = web.Application(); h.register(app)
    async with TestClient(TestServer(app)) as c:
        c.session.cookie_jar.update_cookies({COOKIE_NAME: h.sessions.create()})
        r = await c.post("/admin/api/llm/use", json={"kind": "cli"})
        assert r.status == 404
        return await r.json()


@pytest.mark.asyncio
async def test_failed_switch_response_carries_the_key(tmp_path):
    d = await _use_missing_model(tmp_path)
    assert d["msg_key"] == "配置里没有 {name}" and d["msg_params"] == {"name": "ClaudeCLI"}
    assert d["error"] == "配置里没有 ClaudeCLI"


@pytest.mark.asyncio
async def test_failed_switch_shows_in_english(tmp_path):
    from tests.admin.i18n_node import render as render_js
    (out,) = render_js([await _use_missing_model(tmp_path)], "en")
    assert out and not has_chinese(out), out



@pytest.mark.asyncio
async def test_grokbot_list_error_shows_in_english(tmp_path):
    """GrokBotError 經 bots_error_msg 到頁面：英文頁面整句是英文。"""
    from core.admin.grokbot import GrokBotAdmin
    from core.agent.agent_session import ErrorKind
    from core.agent.grokbot_client import GrokBotError
    from tests.admin.i18n_node import render as render_js

    admin = GrokBotAdmin(tmp_path / "creds.json")

    async def refuse():
        raise GrokBotError("续期被拒绝（HTTP {status}），需要重新登录", ErrorKind.AUTH, status=401)

    admin.client().list_agents = refuse
    with pytest.raises(GrokBotError) as e:
        await admin.fetch_bots()
    assert e.value.detail == "续期被拒绝（HTTP 401），需要重新登录"
    status = admin.status()
    assert status["bots_error"] == e.value.detail
    out = render_js([status["bots_error_msg"], msg("读取 Bot 列表失败：{error}", error=e.value.payload())], "en")
    assert all(o and not has_chinese(o) and "401" in o for o in out), out
    await admin.close()


# msg()／KeyedError 的參數名不能是 key；SearchError、GrokBotError 自己還用了 kind（和 spoken）
RESERVED = {"msg": {"key"}, "UIError": {"key"}, "KeyedError": {"key"}, "UpdateError": {"key"},
            "WeatherError": {"key"}, "SearchError": {"key", "kind", "spoken"}, "GrokBotError": {"key", "kind"}}


def test_message_templates_avoid_reserved_parameter_names():
    assert set(RESERVED) == KEY_CONSTRUCTORS
    bad = [f"{name}:{call.lineno}" for name, _lines, roots in python_sources() for root in roots
           for call in key_calls(root)
           if set(re.findall(r"\{(\w+)\}", call.args[0].value))
           & RESERVED[call.func.id if isinstance(call.func, ast.Name) else call.func.attr]]
    assert not bad, bad


ALLOW = Path(__file__).with_name("i18n-fstring-allow.txt")
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "_log"}


def _str_const(n):
    return isinstance(n, ast.Constant) and isinstance(n.value, str)


def _chinese_literal(n):
    """一個字串字面值或 f-string，裏面的固定文字含中文（包括只有全形標點的）。"""
    if _str_const(n):
        return has_chinese(n.value)
    return isinstance(n, ast.JoinedStr) and any(_str_const(v) and has_chinese(v.value) for v in n.values)


def _chinese_formatting(tree):
    """含中文的 f-string、`中文 + 變數`、`x += 中文`、`"中文".join()`、`"中文{}".format()`、`"中文%s" % x`。

    日誌呼叫的參數除外。
    """
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LOG_METHODS:
            for arg in node.args:
                skip.update(id(n) for n in ast.walk(arg))
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.JoinedStr) and _chinese_literal(node):
            yield node
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            sides = [node.left, node.right]
            if any(_chinese_literal(s) for s in sides) and not all(_str_const(s) for s in sides):
                yield node
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add) and _chinese_literal(node.value):
            yield node
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("format", "join") \
                and _str_const(node.func.value) and has_chinese(node.func.value.value):
            yield node


def _scanned():
    """(檔名, 原始碼各行, 要檢查的節點)：python_sources() 的每一個範圍。"""
    for name, lines, roots in python_sources():
        yield name, lines, [n for root in roots for n in _chinese_formatting(root)]


def _allowed():
    return {"\t".join(line.split("\t", 2)[:2])
            for line in ALLOW.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")}


def test_no_chinese_sentence_is_formatted_outside_msg():
    allowed, found = _allowed(), []
    for name, lines, nodes in _scanned():
        for node in nodes:
            code = lines[node.lineno - 1].strip()
            if f"{name}\t{code}" not in allowed:
                found.append(f"{name}:{node.lineno}: {code}")
    assert not found, "改用 msg()／UIError，或在 i18n-fstring-allow.txt 寫明原因：\n" + "\n".join(found)


def test_allow_list_has_no_stale_entries():
    present = {f"{name}\t{lines[n.lineno - 1].strip()}" for name, lines, nodes in _scanned() for n in nodes}
    for entry in _allowed():
        assert entry in present, f"已不存在：{entry}"


def test_guard_catches_every_shape():
    src = '\n'.join(['a = f"缺少 {x}"', 'b = "前缀：" + x', 'c = "；" + x', 'n += "。后面"', 'd = "、".join(xs)',
                     'e = "第 {} 个".format(x)', 'g = "共 %d 个" % x', 'h = f"{x}（API）"', 'log.info(f"日志 {x}")',
                     'ok = "纯中文" + "常量"'])
    assert sorted({n.lineno for n in _chinese_formatting(ast.parse(src))}) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_key_constructors_cover_every_keyed_error():
    """python_sources() 裏每一個繼承 KeyedError／UIError 的類別都在 KEY_CONSTRUCTORS：漏掉的話它的鍵不會被擷取。"""
    keyed = {"KeyedError", "UIError"}
    found = set()
    for _name, _lines, roots in python_sources():
        for root in roots:
            for node in ast.walk(root):
                if isinstance(node, ast.ClassDef) and any(
                        (b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")) in keyed for b in node.bases):
                    found.add(node.name)
    assert found <= KEY_CONSTRUCTORS, found - KEY_CONSTRUCTORS


def test_message_keys_are_literals():
    bad = [f"{name}:{call.lineno}" for name, _lines, roots in python_sources() for root in roots
           for call in key_calls(root)
           if not (call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str))]
    assert not bad, bad


def test_model_label_msg_translates_only_vendor_labels():
    from core.admin import catalog
    from core.agent import llm_router
    vendor = catalog.LLM_VENDORS[0]
    assert llm_router.model_label_msg(vendor["entry"]) == {"msg_key": vendor["label"], "msg_params": {}}
    # 条目名是配置里的名字：恰好和字典里的词一样也原样显示
    assert llm_router.model_label_msg("默认") == {"msg_key": "{value}", "msg_params": {"value": "默认"}}


# ---------- 英文介面的顯示（spec §6.1）：我們的句子是英文，用戶的值原樣保留 ----------

def _cjk_free(s):
    return not has_chinese(s)   # 全形標點也算：英文頁面上不該出現（）、：


def _error_of(fn):
    with pytest.raises(UIError) as e:
        fn()
    return e.value.payload()


def _en(m):
    (out,) = render_js([m], "en")
    return out


def test_speech_errors_show_in_english(tmp_path):
    path = tmp_path / "config.yaml"; path.write_text("{}\n")
    merged = config_store.load_merged(custom_path=path)
    bad_url = _error_of(lambda: speech_settings.save("ASR", {"api_key": "k", "base_url": "not a url"}, merged, path))
    no_key = _error_of(lambda: speech_settings.save("ASR", {"api_key": ""}, merged, path))
    assert bad_url["msg_key"].startswith("Base URL 必须是完整的 HTTP(S) 网址") and no_key["msg_key"] == "请填写 API Key"
    out = render_js([bad_url, no_key], "en")
    assert all(_cjk_free(s) and s for s in out), out


def test_mcp_error_shows_in_english():
    bad = _error_of(lambda: mcp_settings.validate({"url": "ftp://x"}, {}))
    assert bad["msg_key"] == "请使用 HTTP(S) 网址；令牌请放在 headers，不要放在网址里"
    out = _en(bad)
    assert _cjk_free(out) and out, out


# 用户的值和设备认的口令：英文页面必须原样保留。长的在前：先去掉「继续上次的话题」再去掉它的一部分
USER_VALUES = ("好一会儿没听到你说话，我先休息啦，有事再叫我。",   # end_prompt 的告别语（config.yaml）
               "继续上次的话题", "接着上次聊",                     # resume_keywords
               "克劳德", "我在", "退出", "关闭", "停下")            # 称呼、呼名的回复、exit_commands、停下的口令


def test_pipeline_values_show_in_english(tmp_path):
    from tests.admin.test_i18n_catalog import describe_all
    for name, d in describe_all(tmp_path).items():
        values = [s[k] for s in d["flow"]["stages"] for k in ("title", "value", "hint")]
        values += [t[k] for t in d["flow"]["tips"] for k in ("title", "say", "result")]
        values += [s[k] for s in d["prompt"]["segments"] for k in ("title", "when", "note")] + d["prompt"]["warnings"]
        values.append(d["prompt"]["llm"])
        out = render_js(values, "en")
        leftover = []
        for o in out:
            for v in USER_VALUES:
                o = o.replace(v, "")
            if not _cjk_free(o):
                leftover.append(o)
        assert not leftover, (name, leftover)


def test_resume_keyword_stays_chinese_and_voice_name_is_translated(tmp_path):
    from tests.admin.test_i18n_catalog import describe_all
    d = describe_all(tmp_path)
    tip = next(t for t in d["claude_cli"]["flow"]["tips"] if t["title"] == "接着上次聊")
    title, result = render_js([tip["title"], tip["result"]], "en")
    assert _cjk_free(title), title                      # 作為標題是我們的詞，翻譯
    assert "接着上次聊" in result                       # 作為參數是設備認的口令，原樣
    tts = next(s for s in d["edge_api"]["flow"]["stages"] if s["id"] == "tts")
    value = _en(tts["value"])
    assert value.startswith("EdgeTTS · ") and _cjk_free(value), value
    long_task = next(t for t in d["claude_cli"]["flow"]["tips"] if t["title"] == "长任务")
    result = _en(long_task["result"])
    assert "停下" in result or "继续" in result         # spoken commands are kept as the device hears them


def test_update_error_shows_in_english():
    from core.admin import updater
    e = updater.UpdateError("已经在更新了，请等它结束")
    out = _en(e.payload())
    assert out and _cjk_free(out), out


DOUBAO_CONFIG = {"type": "doubao_stream", "api_key": "test-key", "resource_id": "volc.seedasr.sauc.duration"}


@pytest.mark.asyncio
async def test_doubao_connection_error_shows_in_english(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("refused")

    # check_connection() 的網址寫死在 ASRProvider 裏（wss://openspeech.bytedance.com/…），不能靠設定改，
    # 所以換掉連線函式：測試不連外網、結果固定
    monkeypatch.setattr(doubao_stream.websockets, "connect", refuse)
    result = await doubao_stream.check_connection(DOUBAO_CONFIG, timeout=1)
    m = result["error_msg"]
    assert m["msg_key"].startswith("连不上豆包识别服务")
    detail = str(m["msg_params"].get("detail", ""))
    assert "refused" in detail
    out = _en(m)
    assert out and _cjk_free(out.replace(detail, "")), out


def test_plugin_field_error_names_the_field_in_english(tmp_path):
    from core.admin import plugin_settings
    path = tmp_path / "c.yaml"; path.write_text("{}\n")
    merged = config_store.load_merged(custom_path=path)
    err = _error_of(lambda: plugin_settings._values("get_weather", {"location": "a\nb"}, merged))
    assert err["msg_key"] == "{label} 不能换行"
    out = _en(err)
    assert _cjk_free(out) and out.lower().startswith("location"), out    # 「所在地」經 label() 翻成英文


def test_duplicate_call_name_error_in_english(tmp_path):
    from core.admin import pages
    path = tmp_path / "c.yaml"; path.write_text(CLI_YAML)
    # 兩個能配稱呼的模型：本機 Claude Code，加上填好的 DeepSeek
    pages.save_vendor("deepseek", {"base_url": "https://api.deepseek.com", "api_key": "sk-1234567890abcdef",
                                   "model": "deepseek-chat"}, config_store.load_merged(custom_path=path), path)
    merged = config_store.load_merged(custom_path=path)
    payload = {"models": [{"entry": "ClaudeCLI", "aliases": ["小克"]}, {"entry": "DeepSeekLLM", "aliases": ["小克"]}]}
    err = _error_of(lambda: pages.save_call_names(payload, merged, path))
    assert err["msg_key"] == "称呼“{alias}”同时给了 {taken} 和 {label}，只能给一个模型"
    out = _en(err)
    assert "小克" in out and _cjk_free(out.replace("小克", "")), out     # 稱呼是用戶的值，其餘（模型標籤）要是英文


def test_plugin_errors_in_english():
    from plugins_func.functions.get_weather import WeatherError
    from plugins_func.functions.web_search import SearchError
    for e in (SearchError("还没有填写 API Key"), WeatherError("没找到「{name}」这个地方", name="Atlantis")):
        out = _en(e.payload())
        assert out and _cjk_free(out), out


def test_short_key_hint_in_english():
    short, long = config_store.masked_fields("key_hint", "abc"), config_store.masked_fields("key_hint", "sk-" + "x" * 30)
    assert _cjk_free(_en(short["key_hint_msg"]))
    assert "key_hint_msg" not in long and long["key_hint"].startswith("sk-x")


def _admin(tmp_path, text):
    """用 text 作設定的後台，運行中的設定就是合併後的設定（沒有 ws_server：熱加載只寫進運行中的設定）。"""
    path = tmp_path / ".config.yaml"; path.write_text(text)
    h = AdminHandler(config_store.load_merged(custom_path=path), config_path=path)
    app = web.Application(); h.register(app)
    return h, app


@pytest.mark.asyncio
async def test_incomplete_asr_model_error_in_english(tmp_path):
    missing = tmp_path / "no-model"
    h, app = _admin(tmp_path, f"ASR:\n  SherpaXASR:\n    type: sherpa_onnx_local\n    model_dir: {missing}\n")
    async with TestClient(TestServer(app)) as c:
        c.session.cookie_jar.update_cookies({COOKIE_NAME: h.sessions.create()})
        r = await c.post("/admin/api/asr/use", json={"choice": "local"})
        assert r.status == 400
        d = await r.json()
    assert d["msg_key"] == "X-ASR 模型文件不完整（{detail}），请重新运行安装脚本下载"
    assert d["msg_params"]["detail"]["msg_key"] == "缺少 {path}"      # 嵌套的句子也帶 key
    out = _en(d)
    assert str(missing) in out and _cjk_free(out), out


@pytest.mark.asyncio
async def test_intent_save_note_is_the_apply_sentence(tmp_path):
    """改插件、意圖已在使用：回應的 note_msg 是熱加載那一句，不是「已经在使用」，英文頁面顯示英文。"""
    from tests.admin.test_admin_pipeline import API_YAML
    h, app = _admin(tmp_path, API_YAML)
    async with TestClient(TestServer(app)) as c:
        c.session.cookie_jar.update_cookies({COOKIE_NAME: h.sessions.create()})
        r = await c.post("/admin/api/intent", json={"mode": "function_call", "functions": ["get_weather"]})
        assert r.status == 200
        d = await r.json()
    assert d["note_msg"]["msg_key"] == "已保存，重启服务后生效" and d["note"] == "已保存，重启服务后生效"
    out = _en(d["note_msg"])
    assert out and _cjk_free(out), out


@pytest.mark.asyncio
async def test_kiosk_switch_refused_shows_in_english(tmp_path, monkeypatch):
    """llm_router.switch() 回傳 False（模型沒配置好）：錯誤句子和原因都是英文。"""
    from types import SimpleNamespace

    from core.admin import connections
    from core.agent import llm_router
    from core.agent.session_store import SessionStore

    monkeypatch.setattr(llm_router, "_store", SessionStore(tmp_path / "agent_sessions.json", "last_model"))
    monkeypatch.setattr(llm_router, "switch", lambda conn, name, remember=True: False)
    _h, app = _admin(tmp_path, CLI_YAML)
    fake = SimpleNamespace(session_id="s-1", device_id="dev-1", client_ip="127.0.0.1")
    connections.register(fake)
    try:
        async with TestClient(TestServer(app)) as c:
            r = await c.post("/kiosk/llm/use", json={"kind": "cli", "device": "dev-1"})
            assert r.status == 500
            d = await r.json()
    finally:
        connections.unregister(fake)
    assert d["msg_key"] == "切换失败：{error}" and d["msg_params"]["error"] == {"msg_key": "模型没有配置好", "msg_params": {}}
    out = _en(d)
    assert out and _cjk_free(out), out
