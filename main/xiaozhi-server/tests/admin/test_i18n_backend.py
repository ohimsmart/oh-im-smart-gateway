"""後端訊息：UIError 帶 key；錯誤回應帶 msg_key；英文介面顯示英文。"""
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import config_store, mcp_settings, speech_settings
from core.admin.admin_handler import COOKIE_NAME, AdminHandler
from core.admin.i18n_msg import UIError, msg, render


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
