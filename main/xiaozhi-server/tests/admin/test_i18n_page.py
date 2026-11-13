"""index.html：後端送來的顯示欄位都經 UII18n.text()／t()，用戶的值列在 i18n-page-allow.txt。"""
import re
from pathlib import Path

from tests.admin.i18n_node import has_chinese

INDEX = Path(__file__).resolve().parents[2] / "core" / "admin" / "static" / "index.html"
ALLOW = Path(__file__).with_name("i18n-page-allow.txt")
FIELDS = ("label", "name", "hint", "desc", "title", "note", "detail", "message", "error", "unsupported",
          "legacy_note", "say", "result", "when", "source", "value", "reason", "llm", "gender", "style",
          "model_label", "bots_error", "key_hint", "token_hint", "hint_set")
# 已經翻譯的部分先拿掉：UII18n.text(...)、UII18n.t(...)、UII18n.msg(...)（允許一層巢狀括號）
TRANSLATED = re.compile(r"UII18n\.(?:text|t|msg)\((?:[^()]|\([^()]*\))*\)")
# 會顯示出來的位置：${...}、esc(...)、toast(...)、textContent = ...;（同樣允許一層巢狀括號）
SHOWN = re.compile(r"\$\{((?:[^{}]|\{[^{}]*\})*)\}|esc\(((?:[^()]|\([^()]*\))*)\)|toast\(((?:[^()]|\([^()]*\))*)\)"
                   r"|textContent\s*=\s*([^;]+);")
# x.note、x.note_msg（_msg 物件直接顯示會變成 [object Object]）
FIELD = re.compile(r"([A-Za-z_$][\w$]*(?:\.[\w$]+|\[[^\]]+\])*\.(?:%s)(?:_msg)?)\b(?!\s*\()" % "|".join(FIELDS))
# esc(w)、esc(labelOf(x))：看不出內容是甚麼，一律列出，由 allow 檔逐條說明
OPAQUE = re.compile(r"^\s*([A-Za-z_$][\w$]*(?:\((?:[^()]|\([^()]*\))*\))?)\s*$")
ESC = re.compile(r"esc\(((?:[^()]|\([^()]*\))*)\)")


def untranslated(code: str):
    """code 裏顯示出來、但沒經過 UII18n 的欄位讀取（三元運算和 || 的兩邊都算）。"""
    code = TRANSLATED.sub("''", code)
    for m in SHOWN.finditer(code):
        expr = next(g for g in m.groups() if g is not None)
        yield from (f.group(1) for f in FIELD.finditer(expr))
        # 只看 esc(...) 的內容（${n} 這種數字很常見）；esc 可能就是這一處，也可能在 ${...} 裏面
        inner = ([expr] if m.group(2) is not None else []) + [e.group(1) for e in ESC.finditer(expr)]
        yield from ("esc(" + x.strip() + ")" for x in inner if OPAQUE.match(x))


def _allowed():
    return {line.split("\t", 1)[0] for line in ALLOW.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")}


def test_guard_sees_every_shape():
    assert list(untranslated("esc(c.detail)")) == ["c.detail"]
    assert list(untranslated('toast(r.changed.length ? r.note : UII18n.t("没有改动"))')) == ["r.note"]
    assert list(untranslated("`<b>${o.label}</b>`")) == ["o.label"]
    assert list(untranslated("el.textContent = x.message;")) == ["x.message"]
    assert list(untranslated("esc(x.note_msg || x.note)")) == ["x.note_msg", "x.note"]
    assert list(untranslated("el.textContent = x.note_msg;")) == ["x.note_msg"]
    assert list(untranslated("esc(w)")) == ["esc(w)"]
    assert list(untranslated("`<i>${esc(w)}</i>`")) == ["esc(w)"]
    assert list(untranslated("`${n} ${esc(UII18n.text(w))}`")) == []
    assert list(untranslated("esc(labelOf(d.default))")) == ["esc(labelOf(d.default))"]
    assert list(untranslated("esc(UII18n.text(labelOf(d.default)))")) == []
    assert list(untranslated("esc(UII18n.text(x.note_msg || x.note))")) == []
    assert list(untranslated('toast(UII18n.text(r.note_msg || r.note))')) == []
    assert list(untranslated("`${UII18n.t(\"默认是\")} ${esc(UII18n.text(a.label))}`")) == []


def test_backend_strings_are_translated_on_the_page():
    allowed, found = _allowed(), []
    for no, line in enumerate(INDEX.read_text(encoding="utf-8").splitlines(), 1):
        found += [f"{no}: {e}" for e in untranslated(line) if e not in allowed]
    assert not found, "改為 UII18n.text(...)，或把用戶的值列進 i18n-page-allow.txt：\n" + "\n".join(found)


def test_page_allow_list_has_no_stale_entries():
    present = {e for line in INDEX.read_text(encoding="utf-8").splitlines() for e in untranslated(line)}
    for expr in _allowed():
        assert expr in present, f"已不存在：{expr}"


SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
T_LITERAL = re.compile(r'UII18n\.t\(\s*(?:"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')\s*\)')
MSG_KEY = re.compile(r'msg_key:\s*(?:"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')')
COMMENT = re.compile(r"(?:^|\s)//.*$")
SCRIPT_ALLOW = Path(__file__).with_name("i18n-script-allow.txt")


def test_no_chinese_in_scripts_outside_translation_calls():
    """<script> 裏的中文（包括模板裏的全形標點）只能在 UII18n.t("…") 或 msg_key 字面值裏，註解除外。"""
    allowed = {line.split("\t", 1)[0] for line in SCRIPT_ALLOW.read_text(encoding="utf-8").splitlines()
               if line and not line.startswith("#")}
    found = []
    html = INDEX.read_text(encoding="utf-8")
    for block in SCRIPT.finditer(html):
        first = html[:block.start(1)].count("\n") + 1
        for i, line in enumerate(block.group(1).splitlines()):
            rest = COMMENT.sub("", MSG_KEY.sub("", T_LITERAL.sub("", line)))
            if has_chinese(rest) and line.strip() not in allowed:
                found.append(f"{first + i}: {line.strip()}")
    assert not found, "把整句放進一個 UII18n.t() 範本或 UII18n.msg({msg_key: …})：\n" + "\n".join(found)
