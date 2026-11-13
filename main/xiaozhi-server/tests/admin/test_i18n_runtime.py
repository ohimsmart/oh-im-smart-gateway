"""runtime.js：語言判斷、三選一字典、{msg_key, msg_params} 的翻譯。"""
import json
import shutil
import subprocess

import pytest

from tests.admin.i18n_node import I18N

_DETECT = r"""
const vm = require('vm'), fs = require('fs');
const [dir, saved, search, language] = process.argv.slice(1);
const store = saved ? { 'thinksmart.uiLocale': saved } : {};
const sb = { window: {}, URLSearchParams, localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
  location: { search }, navigator: { language } };
vm.createContext(sb);
vm.runInContext(fs.readFileSync(dir + '/runtime.js', 'utf8'), sb);
process.stdout.write(JSON.stringify({ locale: sb.window.UII18n.locale, stored: store['thinksmart.uiLocale'] ?? null }));
"""


def detect(saved="", search="", language="zh-CN"):
    r = subprocess.run([shutil.which("node"), "-e", _DETECT, str(I18N), saved, search, language],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("language,expected", [
    ("en-US", "en"), ("en-HK", "en"), ("fr", "en"), ("", "en"),
    ("zh-CN", "zh-Hans"), ("zh", "zh-Hans"), ("zh-Hans-HK", "zh-Hans"),
    ("zh-TW", "zh-Hant"), ("zh-HK", "zh-Hant"), ("zh-MO", "zh-Hant"), ("zh-Hant-TW", "zh-Hant")])
def test_browser_language(language, expected):
    assert detect(language=language)["locale"] == expected


def test_remembered_value_and_lang_parameter():
    assert detect(saved="en", language="zh-CN")["locale"] == "en"
    assert detect(saved="en", search="?lang=zh-Hant")["locale"] == "en"     # remembered wins on the admin page
    r = detect(search="?lang=en", language="zh-CN")
    assert r == {"locale": "en", "stored": "en"}
    assert detect(saved="xx", search="?lang=xx", language="zh-TW")["locale"] == "zh-Hant"   # invalid values are ignored


def test_msg_translates_nested_parameters(tmp_path, monkeypatch):
    from tests.admin import i18n_node
    for f in ("zh-Hant.js", "en.js", "runtime.js"):
        (tmp_path / f).write_text((I18N / f).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "en.js").write_text('window.UI_EN = {"已更新到 {version}": "Updated to {version}", '
                                    '"{why}，已退回": "{why}; rolled back", "默认": "default", '
                                    '"说{said}接上": "Say {said} to resume", "接着上次聊": "pick up where we left off"};',
                                    encoding="utf-8")
    monkeypatch.setattr(i18n_node, "I18N", tmp_path)
    out = i18n_node.render([
        {"msg_key": "已更新到 {version}", "msg_params": {"version": "1.0.1"}},
        {"msg_key": "{why}，已退回", "msg_params": {"why": {"msg_key": "已更新到 {version}", "msg_params": {"version": "2"}}}},
        "默认", "不在字典里", None, 3, {"msg_key": "缺参数 {x}"},
        # a keyword the device listens for stays as typed, even though it is also a dictionary key
        {"msg_key": "说{said}接上", "msg_params": {"said": "接着上次聊"}},
        # an app-owned label is passed as an object and is translated
        {"msg_key": "说{said}接上", "msg_params": {"said": {"msg_key": "接着上次聊"}}}], "en")
    assert out == ["Updated to 1.0.1", "Updated to 2; rolled back", "default", "不在字典里", "", "3", "缺参数 {x}",
                   "Say 接着上次聊 to resume", "Say pick up where we left off to resume"]


def test_render_runs_cleanly():
    from tests.admin import i18n_node
    assert i18n_node.render(["保存"], "zh-Hans") == ["保存"]


_APPLIED = r"""
const vm = require('vm'), fs = require('fs');
const [dir, en] = process.argv.slice(1);
const sb = { window: { UI_EN: JSON.parse(en) }, URLSearchParams, location: { search: '' }, navigator: { language: 'en-US' },
  localStorage: { getItem: () => 'en', setItem: () => {} } };
vm.createContext(sb);
vm.runInContext(fs.readFileSync(dir + '/runtime.js', 'utf8'), sb);
process.stdout.write(sb.window.UII18n.applied + ' ' + sb.window.UII18n.t('登录'));
"""


@pytest.mark.parametrize("ui_en,expected", [({}, "zh-Hans 登录"), ({"登录": "Log in"}, "en Log in")])
def test_locale_without_a_dictionary_falls_back_to_simplified(ui_en, expected):
    r = subprocess.run([shutil.which("node"), "-e", _APPLIED, str(I18N), json.dumps(ui_en, ensure_ascii=False)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout == expected
