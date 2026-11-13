"""字典完整性（spec §6.4）：原文的每一條都有繁中／简中和英文，英文字典沒有多餘的鍵。"""
import ast
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from core.admin import config_store, pipeline
from tests.admin.i18n_node import KEY_CONSTRUCTORS, catalogs, has_chinese as _cjk, python_sources   # 中文＝表意文字＋CJK 標點＋全形字元

SERVER = Path(__file__).resolve().parents[2]
ADMIN = SERVER / "core" / "admin"
INDEX = ADMIN / "static" / "index.html"
ATTRS = ("title", "aria-label", "placeholder", "data-kb-enter")
DISPLAY = {"title", "value", "hint", "say", "result", "source", "when", "note", "llm"}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


class _Markup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.in_body = set(), [], False

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        skip = tag in ("script", "style", "textarea") or "data-no-i18n" in a
        if tag not in self.VOID:   # 沒有結束標籤的元素不入棧，否則之後的 pop 會錯位
            self.stack.append(skip or (self.stack[-1] if self.stack else False))
        if tag == "body":
            self.in_body = True
        if self.in_body and not tag in ("script", "style"):
            self.out.update(v for k, v in a.items() if k in ATTRS and v)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        text = data.strip()
        if text and (self.in_body and not (self.stack and self.stack[-1]) or self.lasttag == "title"):
            self.out.add(text)


def _js_literals(html):
    out = set()
    lit = r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')'
    for pattern in (r'UII18n\.t\(\s*' + lit + r'\s*\)', r'msg_key:\s*' + lit):
        for m in re.finditer(pattern, html):
            out.add(ast.literal_eval(m.group(1)))   # JS 和 Python 的簡單字串跳脫寫法相同（\" \' \\ \n）
    return out


DISPLAY_KEYS = {"note", "error", "message", "detail", "label", "name", "hint", "desc", "title", "unsupported"}


def _chinese_constants(expr):
    """expr 裏所有含中文的字串常數（f-string 的片段除外：那些由 Task 6 的守門測試處理）。"""
    inside_fstring = {id(v) for n in ast.walk(expr) if isinstance(n, ast.JoinedStr) for v in n.values}
    return {n.value for n in ast.walk(expr) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in inside_fstring and _cjk(n.value)}


def _display_key(node):
    return isinstance(node, ast.Constant) and node.value in DISPLAY_KEYS


def _python_literals():
    out = set()
    for _name, _lines, roots in python_sources():
        for root in roots:
            for node in ast.walk(root):
                if isinstance(node, ast.Call) and node.args:
                    f = node.func
                    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                    if name in KEY_CONSTRUCTORS | {"label", "ValueError", "_error"} \
                            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        out.add(node.args[0].value)
                    elif name == "_check":   # status._check(名稱, ok, 說明)：名稱和說明都按位置傳
                        for arg in node.args:
                            out |= _chinese_constants(arg)
                # 直接放進顯示欄位的常數：{"note": "已取消"}、result["error"] = "更新失败，没有输出"
                elif isinstance(node, ast.Dict):
                    for k, v in zip(node.keys, node.values):
                        if k is not None and _display_key(k):
                            out |= _chinese_constants(v)
                elif isinstance(node, ast.Assign):
                    if any(isinstance(t, ast.Subscript) and _display_key(t.slice) for t in node.targets):
                        out |= _chinese_constants(node.value)
                # 區域變數再傳進 label()（pages.py:453 的 name = "X-ASR 中英混合"）由上面的 label() 規則收不到：
                # Task 6 改寫時字面值直接寫在 label() 裏，或寫成 label("X-ASR 中英混合") 再賦值
    return out


def test_python_literals_sees_every_shape():
    src = '\n'.join(['a = label("甲")', 'b = {"note": "乙", "at": 1}', 'r["error"] = x if y else "丙"',
                     'c = {"note": f"丁{x}"}', 'd = {"other": "戊"}', 'e = UIError("己")',
                     'f = _check("庚", ok, x or "辛")'])
    from unittest import mock
    with mock.patch(__name__ + ".python_sources", lambda: [("t.py", [], [ast.parse(src)])]):
        assert _python_literals() == {"甲", "乙", "丙", "己", "庚", "辛"}


def _catalog_labels():
    """頁面會顯示的 catalog 表格。必有的鍵直接索引（表格結構改了就報錯），選填的才用 get。"""
    from core.admin import catalog, pages
    from core.agent import reply_language
    out = {v[k] for v in catalog.EDGE_VOICES for k in ("name", "group", "gender", "style")}
    out |= {o["label"] for o in (*catalog.CLAUDE_MODELS, *catalog.CLAUDE_EFFORTS, *catalog.CLAUDE_TOOLS,
                                 *catalog.LLM_VENDORS, *catalog.DOUBAO_RESOURCES)}
    out |= {o[k] for o in (*catalog.CLAUDE_MODELS, *catalog.LLM_VENDORS, *catalog.DOUBAO_RESOURCES)
            for k in ("hint",) if o.get(k)}
    out |= {o[k] for o in catalog.CLAUDE_LEVELS for k in ("label", "desc")}
    out |= {p[k] for p in (*catalog.PLUGINS, *catalog.ALWAYS_ON_PLUGINS) for k in ("label", "desc")}
    for s in catalog.PLUGIN_SETTINGS.values():
        out |= {s[k] for k in ("note", "legacy_note") if s.get(k)}
        out |= {f[k] for f in s["fields"] for k in ("label", "hint") if f.get(k)}
    out |= set(catalog.EMOTION_LABELS.values()) | set(pages.SHERPA_MODEL_LABELS.values())
    out |= {o["label"] for o in reply_language.OPTIONS}
    # 若哪個表格讀空了（改名、搬位置），這裏會失敗，而不是靜靜少收
    assert {"所在地", "大笑", "查日期", "只读", "读文件", "2.0 小时版", "最快，适合语音"} <= out
    return out


def not_ui():
    from core.agent import reply_language
    return set(reply_language.RULES.values()) | {v for p in reply_language.PHRASES.values() for v in p.values()}


def endonym_keys():
    from core.agent import reply_language
    return {o["label"] for o in reply_language.OPTIONS}


def _walk_display(value, out, field=None):
    if isinstance(value, dict):
        if isinstance(value.get("msg_key"), str):
            out.add(value["msg_key"])
            for p in (value.get("msg_params") or {}).values():
                _walk_display(p, out, "param")
            return
        for k, v in value.items():
            _walk_display(v, out, k)
    elif isinstance(value, list):
        for v in value:
            _walk_display(v, out, field)
    elif isinstance(value, str) and field in DISPLAY | {"warnings"}:
        out.add(value)


from tests.admin.test_admin_pipeline import API_YAML, CLI_YAML   # ClaudeCLI（type: claude_cli）；API 模型＋function_call

# load_merged() 把 custom 疊在 config.yaml 之上。config.yaml 裏沒有 ClaudeCLI（它只在安裝範本裏），
# 所以 Claude CLI 那一份要用 CLI_YAML；安裝範本就是用戶的 data/.config.yaml 的起點。
CONFIGS = {
    "template": (SERVER.parents[1] / "scripts" / "config.template.yaml").read_text(encoding="utf-8"),
    "claude_cli": CLI_YAML,
    "edge_api": "selected_module: {TTS: EdgeTTS, LLM: DeepSeekLLM}\n",   # config.yaml:814、:691
    "api_tools": API_YAML,
}
EXPECT_CLI = {"template": None, "claude_cli": True, "edge_api": False, "api_tools": False}


def describe_all(tmp_path):
    """三份設定各自的 pipeline.describe()；確認 Claude CLI 分支真的走到了。"""
    out = {}
    for name, text in CONFIGS.items():
        path = tmp_path / f"{name}.yaml"; path.write_text(text)
        d = pipeline.describe(config_store.load_merged(custom_path=path))
        is_cli = any(s["id"] == "voice_rules" for s in d["prompt"]["segments"])
        if EXPECT_CLI[name] is not None:
            assert is_cli is EXPECT_CLI[name], name
        out[name] = d
    return out


def _runtime_values(tmp_path):
    out = set()
    for d in describe_all(tmp_path).values():
        _walk_display(d, out)
        out |= {v for v in d["prompt"]["var_labels"].values() if isinstance(v, str)}
    return out


@pytest.fixture(scope="module")
def dicts():
    return catalogs()


# 简体和繁体写法完全相同、所以不需要繁中条目的原文。测试列出缺少的条目时，逐条确认后才加进来。
SAME_IN_BOTH = {
    " <span class=\"pill\">忙</span>", " 秒",
    "<div class=\"section-title\">新版本</div><div class=\"card\" id=\"up-box\"></div>",
    "<span class=\"muted\">未知</span>", "<span class=\"muted\">正在真搜一次，最多等 30 秒…</span>",
    "<span class=\"pill ok\">使用中</span>", "<span class=\"pill ok\">全部正常</span>",
    "<span class=\"pill ok\">已配置</span>", "<span class=\"pill ok\">正常</span>",
    "<span class=\"pill warn\">注意</span>", "<span class=\"pill\">已配置</span>",
    "<span class=\"pill\">未配置</span>", "Bot 缺少 id", "MCP 工具", "OTA 地址", "X-ASR 中英混合", "{a}；{b}",
    "{bot}（Grok Bot）", "{field}：{error}", "{label}：{aliases}", "{name} 缺少 {packages}", "{name}（{group}）",
    "{note}；{tail}", "{n} 秒", "{vendor}（API）", "{version}（{path}）", "“{a}”或“{b}”", "上一代模型", "不到 1 秒", "中",
    "低", "你：", "使用本地 X-ASR", "例如 claude-sonnet-5-5", "停止", "分", "分支", "友好", "取消", "句", "合成", "呼名", "哭泣", "困",
    "困惑", "在", "填完整模型名", "大模型", "大笑", "女", "好吃", "字", "小智 Hub", "工作原理", "工具", "已停止（播放到第", "已取消", "已清空", "已用",
    "已配置", "幽默", "思考", "思考力度只能是 低、中、高、超高、最高", "思考力度：{effort}", "所在地", "搜到", "搞笑", "播放", "收起", "收音", "改成",
    "新版 · API Key", "日期", "明亮", "星期", "最新版本：", "最高", "有新版本", "未配置", "本", "本地 X-ASR", "本地 {model}", "查日期",
    "模型", "正在依次播放：第", "清空", "男", "的", "眨眼", "秒", "秒。", "立即更新", "第", "缺少 device", "缺少 device 或 summary",
    "缺少 {path}", "自信", "表情列表", "豆", "豆包", "超高", "配置", "酷", "音色", "音色：", "高"
}


def sources(tmp_path, dicts):
    html = INDEX.read_text(encoding="utf-8")
    p = _Markup(); p.feed(html)
    found = p.out | _js_literals(html) | _python_literals() | _runtime_values(tmp_path) | _catalog_labels()
    existing = (set(dicts["UI_ZH_HANT"]) | set(dicts["UI_ZH_HANS"])) - not_ui()
    return {s for s in found | existing if _cjk(s)}


def test_every_source_string_has_chinese_and_english(tmp_path, dicts):
    src = sources(tmp_path, dicts)
    # 简繁同形的（「女」「日期」）不需要中文條目：繁中頁面直接顯示原文。逐條列出，每條都要確認兩種寫法真的相同
    no_zh = sorted(s for s in src if s not in dicts["UI_ZH_HANT"] and s not in dicts["UI_ZH_HANS"]
                   and s not in SAME_IN_BOTH)
    no_en = sorted(s for s in src if s not in dicts["UI_EN"])
    assert not no_zh, "繁中／简中字典缺少：\n" + "\n".join(no_zh)
    assert not no_en, "英文字典缺少：\n" + "\n".join(no_en)


def test_english_has_no_unknown_keys(tmp_path, dicts):
    extra = sorted(set(dicts["UI_EN"]) - sources(tmp_path, dicts))
    assert not extra, "英文字典有原文裏沒有的鍵：\n" + "\n".join(extra)


@pytest.mark.parametrize("name", ["UI_EN", "UI_ZH_HANT", "UI_ZH_HANS"])
def test_values_keep_placeholders_and_wake_word(dicts, name):
    for key, value in dicts[name].items():
        assert value.strip(), key
        assert set(PLACEHOLDER.findall(value)) == set(PLACEHOLDER.findall(key)), key
        if "小麦" in key:
            assert "小麦" in value, key
        if name == "UI_EN" and "小麦" not in key:
            shown = re.sub(r"<[^>]+>", "", value)
            if key in endonym_keys():
                # 只能保留鍵裏本來就有的本名（「日語 日本語」→「Japanese 日本語」），不能整句不翻
                assert all(ch in key for ch in shown if _cjk(ch)) and not _cjk(shown.split()[0]), key
            else:
                assert not _cjk(shown), key


def test_prompts_are_not_in_the_english_dictionary(dicts):
    assert not set(dicts["UI_EN"]) & not_ui()
