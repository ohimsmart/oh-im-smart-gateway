"""管理后台"工作原理"页：五步流程和速查卡片按配置生成，提示词拆解的各段和变量标记。全部使用临时配置文件。"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import config_store, connections, pipeline
from core.admin.admin_handler import AdminHandler
from core.admin.i18n_msg import render

CLI_YAML = """\
selected_module:
  LLM: ClaudeCLI
  Intent: nointent
tts_audio_prebuffer_count: 15
LLM:
  ClaudeCLI:
    type: claude_cli
    model: haiku
    effort: low
    workdir: ~/xiaozhi-workspace
    allowed_tools: [Read, Grep, Glob]
    resume_keywords: [接着上次聊]
    first_reply_filler: 好的，我看一下
"""

API_YAML = """\
selected_module:
  LLM: DeepSeekLLM
  Intent: function_call
Intent:
  function_call:
    functions: [get_weather, play_music]
"""


@pytest.fixture
def cli_path(tmp_path) -> Path:
    p = tmp_path / ".config.yaml"
    p.write_text(CLI_YAML, encoding="utf-8")
    return p


def merged(path):
    return config_store.load_merged(custom_path=path)


def marks(text):
    return re.findall(f"{pipeline.MARK_START}(\\w+){pipeline.MARK_SEP}", text)


def test_stages_follow_config(cli_path):
    stages = {s["id"]: s for s in pipeline.describe_flow(merged(cli_path))["stages"]}
    assert list(stages) == ["listen", "asr", "llm", "tts", "play"]
    assert render(stages["listen"]["value"]) == "停顿 800ms 算说完"  # config.yaml 里 SileroVAD 的 min_silence_duration_ms
    assert render(stages["llm"]["value"]) == "Claude Code · Haiku" and render(stages["llm"]["hint"]) == "思考力度：低"
    assert render(stages["play"]["value"]) == "缓冲约 0.9 秒" and stages["play"]["page"] is None
    assert {s["page"] for s in stages.values()} == {"asr", "llm", "tts", None}  # 点击跳到对应的设置页


def test_tips_follow_config(cli_path):
    tips = {t["title"]: {k: render(v) for k, v in t.items()} for t in pipeline.describe_flow(merged(cli_path))["tips"]}
    assert list(tips) == ["唤醒", "换模型", "打断", "长任务", "接着上次聊", "结束"]
    assert "“我在”" in tips["换模型"]["result"] and "cloud" in tips["换模型"]["result"]  # 列出现在的称呼
    assert "10 分钟" in tips["长任务"]["result"] and "30 秒" in tips["长任务"]["result"]
    assert "2 小时内" in tips["接着上次聊"]["result"] and "“接着上次聊”" in tips["接着上次聊"]["result"]
    assert "API" not in tips["接着上次聊"]["result"]  # 没有叫到 API 模型
    assert "“退出”" in tips["结束"]["say"] and "2 分钟没人说话" in tips["结束"]["result"]



def test_tips_and_warning_with_zero_values(tmp_path):
    """超时、空闲关闭填 0 有特别含义（不限制、每轮结束就关），不能显示成"0 秒"或退回默认值。"""
    path = tmp_path / ".config.yaml"
    path.write_text(CLI_YAML.replace("    model: haiku\n", "    model: haiku\n    turn_timeout_seconds: 0\n"
                                     "    idle_timeout_seconds: 0\n    resume_window_minutes: 0\n"), encoding="utf-8")
    m = merged(path)
    tips = {t["title"]: {k: render(v) for k, v in t.items()} for t in pipeline.describe_flow(m)["tips"]}
    assert tips["长任务"]["result"].startswith("设备陪着等，不限时长；")
    assert "自动接上" not in tips["接着上次聊"]["result"]  # 自动接续填 0：只在说接续指令时接上
    assert not pipeline.describe_prompt(m)["warnings"]  # 每轮结束就关进程，提示词每轮都是新的

def test_tips_with_call_names_disabled_and_api(tmp_path):
    path = tmp_path / ".config.yaml"
    path.write_text(API_YAML + "\ncall_names:\n  enabled: false\n", encoding="utf-8")
    flow = pipeline.describe_flow(merged(path))
    tips = {t["title"]: {k: render(v) for k, v in t.items()} for t in flow["tips"]}
    assert "换模型" not in tips and "长任务" not in tips
    assert "DeepSeek" in tips["唤醒"]["result"]
    assert "断开后从头开始" in tips["接着上次聊"]["result"]
    assert render(next(s for s in flow["stages"] if s["id"] == "llm")["value"]) == "DeepSeek"


def test_prompt_segments_for_claude_cli(cli_path):
    p = pipeline.describe_prompt(merged(cli_path))
    assert [s["id"] for s in p["segments"]] == ["claude_preset", "base", "voice_rules", "turn"]
    base = next(s for s in p["segments"] if s["id"] == "base")["text"]
    found = set(marks(base))
    assert {"base_prompt", "today_date", "lunar_date", "emojiList", "language"} <= found
    assert not {"local_address", "weather_info"} & found  # 默认模板不按 IP 查位置和天气
    assert "<identity>" in base and "语音助手规则" in p["segments"][2]["text"]
    assert "hub-base-prompt.txt" in render(next(s for s in p["segments"] if s["id"] == "base")["note"])
    assert not any("台湾" in render(w) for w in p["warnings"]) and any("进程启动时" in render(w) for w in p["warnings"])
    assert p["actual"] is None  # 没有在线设备


def test_prompt_segments_for_api_with_plugins(tmp_path):
    path = tmp_path / ".config.yaml"
    path.write_text(API_YAML, encoding="utf-8")
    m = merged(path)
    p = pipeline.describe_prompt(m)
    assert render(p["llm"]) == "DeepSeek"
    assert [s["id"] for s in p["segments"]] == ["base", "fewshot", "tools", "history"]
    fewshot = next(s for s in p["segments"] if s["id"] == "fewshot")["text"]
    assert fewshot.startswith("<tool_call_examples>") and "用户：给我讲个故事吧" in fewshot  # 和实际写进系统提示的一样
    tools = next(s for s in p["segments"] if s["id"] == "tools")["text"]
    assert "查天气" in tools and "放音乐" in tools and "热点新闻" not in tools
    assert not any("进程启动时" in render(w) for w in p["warnings"])


def test_actual_prompt_from_online_device(cli_path):
    msg = SimpleNamespace(role="system", content="真实的系统提示词")
    conn = SimpleNamespace(session_id="s-p", device_id="dev-p", client_ip="192.168.6.211",
                           dialogue=SimpleNamespace(dialogue=[msg]))
    connections.register(conn)
    try:
        p = pipeline.describe_prompt(merged(cli_path))
        assert p["actual"] == {"device_id": "dev-p", "text": "真实的系统提示词"}
    finally:
        connections.unregister(conn)


async def test_pipeline_endpoint_requires_login(cli_path):
    app = web.Application()
    AdminHandler({"selected_module": {"LLM": "ClaudeCLI"}}, config_path=cli_path).register(app)
    async with TestClient(TestServer(app)) as c:
        assert (await c.get("/admin/api/pipeline")).status == 401
        await c.post("/admin/api/setup", json={"password": "pass-1234"})
        data = await (await c.get("/admin/api/pipeline")).json()
        assert data["ok"] and data["flow"]["stages"] and data["flow"]["tips"] and data["prompt"]["segments"]
        assert data["design_url"].endswith("hub-docs/design.md")


def test_resume_keywords_and_call_names_are_joined_as_messages(tmp_path):
    """两个以上的接续关键词取前两个用“或”连接；称呼清单逐项折叠成 msg，用户的词原样保留。"""
    path = tmp_path / ".config.yaml"
    path.write_text(CLI_YAML.replace("[接着上次聊]", "[甲, 乙, 丙]") + "call_names:\n  models:\n"
                    "    ClaudeCLI: [克劳德, cloud]\n    DeepSeekLLM: [深度]\n", encoding="utf-8")
    tips = {t["title"]: t for t in pipeline.describe_flow(merged(path))["tips"]}
    assert "说“甲”或“乙”也能接上" in render(tips["接着上次聊"]["result"])
    calls = render(tips["换模型"]["result"])
    assert "克劳德、cloud；" in calls and calls.endswith("：深度")
    assert tips["换模型"]["result"]["msg_params"]["aliases"]["msg_key"] == "{a}；{b}"


def test_configured_names_are_passed_through_untranslated(tmp_path):
    """条目名、model_name 是配置里的值：包成只有占位符的 msg，页面不会因为碰巧和字典的键相同而翻译它。"""
    path = tmp_path / ".config.yaml"
    path.write_text("selected_module:\n  LLM: 默认\nLLM:\n  默认:\n    type: openai\n    model_name: 默认\n"
                    "call_names:\n  models:\n    默认: [助手]\n", encoding="utf-8")
    m = merged(path)
    calls = next(t for t in pipeline.describe_flow(m)["tips"] if t["title"] == "换模型")["result"]
    assert calls["msg_params"]["aliases"]["msg_params"]["label"] == {"msg_key": "{value}", "msg_params": {"value": "默认"}}
    llm = next(s for s in pipeline.describe_flow(m)["stages"] if s["id"] == "llm")
    assert llm["value"] == {"msg_key": "{value}", "msg_params": {"value": "默认"}}
    assert render(llm["hint"]) == "默认" and render(pipeline.describe_prompt(m)["llm"]) == "默认"
