"""以 Node 執行後台的 runtime.js 和字典（後台沒有建置步驟，字典是 JavaScript，不是 JSON）。"""
import ast
import json
import shutil
import subprocess
from pathlib import Path

I18N = Path(__file__).resolve().parents[2] / "core" / "admin" / "static" / "i18n"
FILES = ["zh-Hant.js", "en.js"]
# 測試裏的「中文」：表意文字、CJK 標點、全形字元（Global Constraints）
CHINESE = "　-〿一-鿿＀-￯"


def has_chinese(s: str) -> bool:
    return any("　" <= ch <= "〿" or "一" <= ch <= "鿿" or "＀" <= ch <= "￯" for ch in s)


_SCRIPT = r"""
const vm = require('vm'), fs = require('fs');
const [dir, mode, locale] = process.argv.slice(1);
const store = {};
// vm contexts have no host globals: runtime.js needs URLSearchParams
const sandbox = { window: {}, URLSearchParams, localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
  location: { search: '' }, navigator: { language: locale || 'zh-CN' } };
vm.createContext(sandbox);
for (const f of %s) vm.runInContext(fs.readFileSync(dir + '/' + f, 'utf8'), sandbox, { filename: f });
if (mode === 'catalogs') {
  process.stdout.write(JSON.stringify({ UI_ZH_HANT: sandbox.window.UI_ZH_HANT, UI_ZH_HANS: sandbox.window.UI_ZH_HANS,
    UI_EN: sandbox.window.UI_EN }));
} else {
  sandbox.window.localStorage = sandbox.localStorage;
  vm.runInContext('localStorage.setItem("thinksmart.uiLocale", ' + JSON.stringify(locale) + ');', sandbox);
  vm.runInContext(fs.readFileSync(dir + '/runtime.js', 'utf8'), sandbox, { filename: 'runtime.js' });
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  process.stdout.write(JSON.stringify({ locale: sandbox.window.UII18n.locale,
    out: input.map(v => sandbox.window.UII18n.text(v)) }));
}
""" % json.dumps(FILES)


def _node(*args, stdin=""):
    node = shutil.which("node")
    assert node, "這組測試需要 Node 18 以上（後台的翻譯是 JavaScript）"
    r = subprocess.run([node, "-e", _SCRIPT, str(I18N), *args], input=stdin, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def catalogs() -> dict:
    return _node("catalogs")


def render(values: list, locale: str) -> list:
    """用 UII18n.text() 翻譯每個值；locale 經已記住的值選定。"""
    result = _node("render", locale, stdin=json.dumps(values, ensure_ascii=False))
    assert result["locale"] == locale
    return result["out"]


SERVER = Path(__file__).resolve().parents[2]
ADMIN = SERVER / "core" / "admin"
# 後台之外、但錯誤會顯示在後台的範圍：{檔案: {函式名稱}}，None 表示整個檔案
EXTRA = {SERVER / "core" / "providers" / "asr" / "doubao_stream.py": {"check_connection"},
         SERVER / "plugins_func" / "functions" / "web_search.py": None,
         SERVER / "plugins_func" / "functions" / "get_weather.py": None}
# 第一個參數是訊息鍵的呼叫：msg() 和 KeyedError 的每一個子類（新增子類時要加在這裏，
# test_key_constructors_cover_every_keyed_error 會檢查）
KEY_CONSTRUCTORS = {"msg", "UIError", "KeyedError", "UpdateError", "SearchError", "WeatherError"}


def python_sources():
    """(檔名, 原始碼各行, [AST 範圍])：core/admin/*.py（i18n_msg.py 除外，它只是轉送鍵）加上 EXTRA 的函式。"""
    for path in sorted(p for p in ADMIN.glob("*.py") if p.name != "i18n_msg.py"):
        src = path.read_text(encoding="utf-8")
        yield path.name, src.splitlines(), [ast.parse(src)]
    for path, funcs in EXTRA.items():
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        if funcs is None:
            yield path.name, src.splitlines(), [tree]
            continue
        found = [f for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and f.name in funcs]
        assert {f.name for f in found} == funcs, f"{path.name}: 找不到 {funcs}"
        yield path.name, src.splitlines(), found


def key_calls(root):
    """root 裏所有 KEY_CONSTRUCTORS 的呼叫。"""
    for node in ast.walk(root):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name in KEY_CONSTRUCTORS:
                yield node
