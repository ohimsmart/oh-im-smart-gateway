"""後端訊息：UIError 帶 key；錯誤回應帶 msg_key；英文介面顯示英文。"""
import ast
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import config_store, mcp_settings, speech_settings
from core.admin.admin_handler import COOKIE_NAME, AdminHandler
from core.admin.i18n_msg import UIError, msg, render
from tests.admin.i18n_node import KEY_CONSTRUCTORS, has_chinese, key_calls, python_sources


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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="en.js in Task 7")
@pytest.mark.asyncio
async def test_failed_switch_shows_in_english(tmp_path):
    from tests.admin.i18n_node import render as render_js
    (out,) = render_js([await _use_missing_model(tmp_path)], "en")
    assert out and not has_chinese(out), out


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
