"""MCP 表單的欄位 ↔ 設定轉換（static/js/mcp-form.js），以 Node 執行；和後端一起的來回；它的路由。"""
import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import config_store, mcp_settings
from core.admin.admin_handler import AdminHandler
from core.admin.i18n_msg import UIError

JS = Path(__file__).resolve().parents[2] / "core" / "admin" / "static" / "js" / "mcp-form.js"
_RUN = r"""
const vm = require('vm'), fs = require('fs');
const sandbox = {window: {}}; vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox, {filename: 'mcp-form.js'});
const calls = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(calls.map(([fn, ...args]) => sandbox.window.McpForm[fn](...args))));
"""


def call(*calls):
    node = shutil.which("node")
    assert node, "這組測試需要 Node 18 以上"
    r = subprocess.run([node, "-e", _RUN, str(JS)], input=json.dumps(calls), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def edit(tmp_path, saved, change):
    """存進後端；照頁面的做法讀回、用表單改、再存。回傳後端實際存下的那份。"""
    path = tmp_path / "mcp.json"   # (save creates the directory)
    mcp_settings.save(path, "s", saved)
    visible = mcp_settings.describe(path)["servers"]["s"]
    [f] = call(["fromConfig", visible])
    [r] = call(["toConfig", {**f, **change}, visible])
    assert "config" in r, r
    mcp_settings.save(path, "s", r["config"])
    return json.loads(path.read_text())["mcpServers"]["s"]


def edit_untouched(tmp_path, cases):
    """edit() with no change for many settings at once, in two Node runs."""
    paths = [tmp_path / str(i) / "mcp.json" for i in range(len(cases))]
    visible = []
    for path, saved in zip(paths, cases):
        mcp_settings.save(path, "s", saved)
        visible.append(mcp_settings.describe(path)["servers"]["s"])
    forms = call(*[["fromConfig", v] for v in visible])
    for path, r in zip(paths, call(*[["toConfig", f, v] for f, v in zip(forms, visible)])):
        assert "config" in r, r
        mcp_settings.save(path, "s", r["config"])
    return [json.loads(p.read_text())["mcpServers"]["s"] for p in paths]


def new(**fields):
    [f] = call(["fromConfig", {}])
    return call(["toConfig", {**f, **fields}, {}])[0]


def test_new_remote_server_token_gets_bearer_unless_it_has_a_scheme():
    assert new(url=" https://m.example/mcp ", token="abc") == {"config": {
        "url": "https://m.example/mcp", "transport": "streamable-http", "headers": {"Authorization": "Bearer abc"}}}
    assert new(url="https://m.example/mcp", token="Basic eHk=")["config"]["headers"] == {"Authorization": "Basic eHk="}
    assert new(url="https://m.example/mcp", transport="sse") == {"config": {"url": "https://m.example/mcp", "transport": "sse"}}


def test_new_command_server():
    assert new(kind="command", command=" npx ", args="-y\n@scope/server\n", env="API_KEY=abc\n\nEMPTY=") == {"config": {
        "command": "npx", "args": ["-y", "@scope/server"], "env": {"API_KEY": "abc", "EMPTY": ""}}}


@pytest.mark.parametrize("fields,bad", [
    ({"url": "  "}, "url"),
    ({"kind": "command", "command": " "}, "command"),
    ({"kind": "command", "command": "npx", "env": "NOEQUALS"}, "env"),
    ({"kind": "command", "command": "npx", "env": "=value"}, "env"),
    ({"kind": "command", "command": "npx", "env": "K=1\nK=2"}, "env"),
    ({"kind": "command", "command": "npx", "env": "__proto__=x"}, "env"),
])
def test_missing_or_bad_fields_are_reported(fields, bad):
    assert new(**fields) == {"bad": bad}


def test_transport_defaults_follow_the_gateway():
    a, b, c, d = call(["fromConfig", {"url": "https://m.example/sse"}],
                      ["fromConfig", {"url": "https://m.example/mcp", "transport": "http"}], ["fromConfig", {}],
                      ["fromConfig", {"command": "npx", "transport": "sse"}])
    assert (a["transport"], b["transport"], c["transport"], c["kind"]) == ("sse", "streamable-http", "streamable-http", "url")
    assert d["transport"] == "sse"   # kept on a command server for when it goes back to a URL


@pytest.mark.parametrize("value,ok", [
    ({}, True), ({"command": "x", "args": ["a"], "env": {"K": "v"}}, True),
    ({"url": "https://m.example", "headers": {"A": "b"}, "timeout": 5}, True),
    (None, False), ([], False), ("x", False), ({"args": "server.py"}, False), ({"args": [1]}, False),
    ({"env": {"K": 1}}, False), ({"headers": []}, False), ({"url": 5}, False), ({"API_ACCESS_TOKEN": 5}, False),
    # the form shows one argument / one KEY= per line, so these can only be edited as JSON
    ({"command": "x", "args": ["first\nsecond"]}, False), ({"env": {"A=B": "v"}}, False),
    (json.loads('{"env": {"__proto__": "v"}}'), False), ({"env": {"KEY ": "v"}}, False),
    ({"command": "x", "args": ["old", ""]}, False), ({"command": "./a\nb"}, False),
    ({"command": "x", "url": "https://m.example"}, False), ({"command": "", "url": "https://m.example"}, False),
    ({"url": "https://m.example", "transport": "custom"}, False), ({"command": "x", "transport": "stdio"}, True),
])
def test_check_accepts_only_shapes_the_form_can_show(value, ok):
    assert call(["check", value]) == [None if ok else "json"]


def test_untouched_save_returns_the_saved_settings_unchanged(tmp_path):
    remote = {"url": "https://m.example/tool ", "transport": "http", "timeout": 30, "env": {"E": "e-1"},
              "headers": {"Authorization": "Bearer secret-1", "X-Team": "t-1"}}
    command = {"command": "./run me ", "args": ["", " left ", "a b"], "env": {"K": " spaced "},
               "headers": {"H": "h-1"}, "API_ACCESS_TOKEN": "tok", "des": "x"}
    assert edit(tmp_path, remote, {}) == remote
    assert edit(tmp_path, command, {}) == command
    # every mix of the optional fields comes back the same
    optional = {"transport": ["sse", "http", "streamable-http"], "timeout": [5], "des": ["d"],
                "headers": [{"Authorization": "Bearer s"}, {"X-A": "a", "authorization": "b"}],
                "API_ACCESS_TOKEN": ["t"]}
    absent = object()
    cases = [{"url": "https://m.example/mcp", **{k: v for k, v in zip(optional, pick) if v is not absent}}
             for pick in itertools.product(*([absent, *values] for values in optional.values()))]
    cases += [{"command": "npx", **({"args": args} if args else {}), **({"env": env} if env else {})}
              for args in ([], ["a"], ["", "b"], [" x ", "y z"]) for env in ({}, {"K": "v"}, {"K": "", "L": " w "})]
    assert edit_untouched(tmp_path, cases) == cases


def test_adding_an_argument_leaves_the_others_as_they_were(tmp_path):
    cmd = {"command": "uvx", "args": ["", " left ", "a b"]}
    assert edit(tmp_path, cmd, {"args": "\n left \na b\n--debug"})["args"] == ["", " left ", "a b", "--debug"]




def test_a_blank_header_on_a_new_or_moved_server_is_kept_when_it_hid_no_saved_value(tmp_path):
    path = tmp_path / "mcp.json"   # (save creates the directory)
    mcp_settings.save(path, "s", {"url": "https://a.example/mcp", "headers": {"X-Feature": ""}}, create=True)
    assert json.loads(path.read_text())["mcpServers"]["s"]["headers"] == {"X-Feature": ""}
    mcp_settings.save(path, "s", {"url": "https://b.example/mcp", "headers": {"X-Feature": "", "X-New": ""}})
    assert json.loads(path.read_text())["mcpServers"]["s"]["headers"] == {"X-Feature": "", "X-New": ""}   # saved empty, so it was no secret



def test_add_server_does_not_replace_one_with_the_same_name(tmp_path):
    path = tmp_path / "mcp.json"   # (save creates the directory)
    mcp_settings.save(path, "s", {"url": "https://a.example/mcp", "headers": {"Authorization": "Bearer a"}}, create=True)
    with pytest.raises(ValueError):
        mcp_settings.save(path, "s", {"url": "https://b.example/mcp"}, create=True)
    assert json.loads(path.read_text())["mcpServers"]["s"]["headers"] == {"Authorization": "Bearer a"}
    mcp_settings.save(path, "s", {"url": "https://a.example/v2", "headers": {"Authorization": "Bearer a"}})   # an edit still works
    assert json.loads(path.read_text())["mcpServers"]["s"]["url"] == "https://a.example/v2"



@pytest.mark.asyncio
async def test_js_route_serves_only_the_form_script(tmp_path):
    path = tmp_path / ".config.yaml"; path.write_text("{}\n")
    h = AdminHandler(config_store.load_merged(custom_path=path), config_path=path)
    app = web.Application(); h.register(app)
    async with TestClient(TestServer(app)) as c:
        r = await c.get("/admin/js/mcp-form.js")
        assert r.status == 200 and "McpForm" in await r.text()
        assert (await c.get("/admin/js/runtime.js")).status == 404
        assert (await c.get("/admin/js/..%2Fadmin_handler.py")).status == 404
        # the page's "new" flag reaches the backend
        assert (await c.post("/admin/api/setup", json={"password": "test-password"})).status == 200
        body = {"name": "n", "config": {"url": "https://a.example/mcp"}, "new": True}
        assert (await c.post("/admin/api/mcp", json=body)).status == 200
        assert (await c.post("/admin/api/mcp", json=body)).status == 400
        assert (await c.post("/admin/api/mcp", json={**body, "new": False})).status == 200


def test_an_empty_command_key_opens_as_a_command_server():
    # mcp_client.py picks stdio whenever the command key is there, so the form must not show a URL server
    [f] = call(["fromConfig", {"command": "", "url": "https://e.example/mcp"}])
    assert f["kind"] == "command"


def test_a_command_server_with_another_transport_becomes_a_url_server_with_the_form_choice():
    # the backend ignores transport on a command server, so "stdio" can be saved there
    saved = {"command": "npx", "transport": "stdio"}
    [f] = call(["fromConfig", saved])
    [r] = call(["toConfig", {**f, "kind": "url", "url": "https://m.example/mcp"}, saved])
    assert r["config"]["transport"] == "streamable-http"


def resave(path, before, after):
    """存著 before 時再存 after。後端拒絕就回傳它要重填的欄位（設定沒動），否則回傳存下的那份。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {"s": before}}))
    try:
        mcp_settings.save(path, "s", after)
    except UIError as e:
        assert json.loads(path.read_text())["mcpServers"]["s"] == before
        return e.params["keys"]
    return json.loads(path.read_text())["mcpServers"]["s"]


def test_an_env_edit_counts_even_after_switching_to_a_url(tmp_path):
    saved = {"command": "npx", "args": ["a"], "env": {"SECRET": "s", "KEEP": "k"}}
    path = tmp_path / "mcp.json"
    mcp_settings.save(path, "s", saved)
    visible = mcp_settings.describe(path)["servers"]["s"]
    [f] = call(["fromConfig", visible])
    [r] = call(["toConfig", {**f, "env": "", "args": "", "kind": "url", "url": "https://m.example/mcp"}, visible])
    assert r["config"] == {"url": "https://m.example/mcp", "transport": "streamable-http"}


def test_an_unfinished_env_is_reported_only_on_a_command_server():
    saved = {"command": "npx", "env": {"K": ""}}
    [f] = call(["fromConfig", saved])
    on_command, on_url = call(["toConfig", {**f, "env": "K=\nhalf"}, saved],
                              ["toConfig", {**f, "env": "K=\nhalf", "kind": "url", "url": "https://m.example/mcp"}, saved])
    assert on_command == {"bad": "env"}
    assert on_url["config"]["env"] == {"K": ""}


def test_an_empty_api_access_token_is_kept_as_saved(tmp_path):
    path = tmp_path / "mcp.json"
    config = {"url": "https://m.example/mcp", "headers": {"Authorization": "Bearer h"}, "API_ACCESS_TOKEN": ""}
    mcp_settings.save(path, "s", config, create=True)
    assert json.loads(path.read_text())["mcpServers"]["s"] == config


def test_a_blank_secret_goes_back_only_when_nothing_that_reaches_the_server_changed(tmp_path):
    path = tmp_path / "mcp.json"
    saved = {"url": "https://m.example/mcp", "transport": "sse", "timeout": 30, "API_ACCESS_TOKEN": "tok",
             "headers": {"Authorization": "Bearer s", "X-Team": "t"}}
    blank = {**saved, "API_ACCESS_TOKEN": "", "headers": {"Authorization": "", "X-Team": ""}}
    assert resave(path, saved, blank) == saved
    assert resave(path, saved, {**blank, "timeout": 5, "des": "d"}) == {**saved, "timeout": 5, "des": "d"}
    # any other difference, even one that may look harmless (case, a path, a header), asks again
    for change in ({"url": "https://M.example/mcp"}, {"url": "https://m.example/v2"}, {"transport": "streamable-http"},
                   {"headers": {"Authorization": "", "X-Team": "", "Host": "b.internal"}}):
        assert resave(path, saved, {**blank, **change}) == "Authorization, X-Team, API_ACCESS_TOKEN", change
    # a typed value goes through; what is left blank is named
    assert resave(path, saved, {**blank, "headers": {"Authorization": "Bearer new", "X-Team": ""}}) == \
        "X-Team, API_ACCESS_TOKEN"
    moved = {"url": "https://o.example/mcp", "headers": {"Authorization": "Bearer n", "X-Team": "t2"}}
    assert resave(path, saved, moved) == moved
    # a blank saved as blank is no secret
    assert resave(path, {"url": "https://m.example/mcp", "headers": {"X-Feature": ""}},
                  {"url": "https://o.example/mcp", "headers": {"X-Feature": ""}})["headers"] == {"X-Feature": ""}
    # a hand-edited server with headers that are no map can still be replaced (no secret to keep)
    assert resave(path, {"url": "https://m.example/mcp", "headers": ["X-A: b"]},
                  {"url": "https://m.example/mcp"}) == {"url": "https://m.example/mcp"}


def test_env_values_go_back_only_to_the_same_program(tmp_path):
    path = tmp_path / "mcp.json"
    saved = {"command": "npx", "args": ["pkg-a"], "env": {"SECRET": "s", "PATH": "/usr/bin"}}
    blank = {**saved, "env": {"SECRET": "", "PATH": ""}}
    assert resave(path, saved, blank) == saved
    # another command, argument or environment can be another program; a removed variable would
    # let the gateway's own one through
    for change in ({"command": "uvx"}, {"args": ["pkg-b"]}, {"args": ["pkg-a", "--debug"]},
                   {"env": {"SECRET": "", "PATH": "/tmp/x"}}, {"env": {"SECRET": ""}}):
        assert resave(path, saved, {**blank, **change}) in ("SECRET, PATH", "SECRET"), change
    assert resave(path, saved, {"url": "https://m.example/mcp", "env": {"SECRET": ""}}) == "SECRET"


def test_the_form_changing_a_server_asks_for_the_saved_secrets_again(tmp_path):
    saved = {"url": "https://m.example/mcp", "API_ACCESS_TOKEN": "old",
             "headers": {"authorization": "Bearer older", "X-Team": "t-1"}}
    path = tmp_path / "mcp.json"
    mcp_settings.save(path, "s", saved)
    visible = mcp_settings.describe(path)["servers"]["s"]
    [f] = call(["fromConfig", visible])
    assert f["token_set"] is True and f["token"] == ""
    moved, token, cleared, typed_x = call(
        ["toConfig", {**f, "url": "https://other.example/mcp"}, visible],
        ["toConfig", {**f, "token": "new"}, visible],
        ["toConfig", {**f, "token_clear": True, "token": "typed-but-cleared"}, visible],
        ["toConfig", {**f, "token": "new"}, {**visible, "headers": {**visible["headers"], "X-Team": "t-2"}}])
    # a new token drops API_ACCESS_TOKEN and every spelling of Authorization; X-Team was left blank
    assert token["config"]["headers"] == {"X-Team": "", "Authorization": "Bearer new"}
    assert "API_ACCESS_TOKEN" not in cleared["config"] and cleared["config"]["headers"] == {"X-Team": ""}
    for r, lost in ((moved, "authorization, X-Team, API_ACCESS_TOKEN"), (token, "X-Team"), (cleared, "X-Team")):
        with pytest.raises(UIError) as e:
            mcp_settings.save(path, "s", r["config"])
        assert e.value.params["keys"] == lost
    assert json.loads(path.read_text())["mcpServers"]["s"] == saved
    # with X-Team filled in (in the JSON editor) the new token saves
    mcp_settings.save(path, "s", typed_x["config"])
    assert json.loads(path.read_text())["mcpServers"]["s"] == {"url": "https://m.example/mcp",
                                                                "headers": {"X-Team": "t-2", "Authorization": "Bearer new"}}


def test_switching_type_needs_the_token_cleared_or_typed(tmp_path):
    saved = {"url": "https://m.example/mcp", "transport": "sse", "API_ACCESS_TOKEN": "old",
             "headers": {"Authorization": "Bearer t"}, "timeout": 5}
    path = tmp_path / "mcp.json"
    mcp_settings.save(path, "s", saved)
    visible = mcp_settings.describe(path)["servers"]["s"]
    [f] = call(["fromConfig", visible])
    kept, cleared = call(["toConfig", {**f, "kind": "command", "command": "npx"}, visible],
                         ["toConfig", {**f, "kind": "command", "command": "npx", "token_clear": True}, visible])
    with pytest.raises(UIError):
        mcp_settings.save(path, "s", kept["config"])
    mcp_settings.save(path, "s", cleared["config"])
    assert json.loads(path.read_text())["mcpServers"]["s"] == {"transport": "sse", "timeout": 5, "command": "npx"}
    # and back to a URL: the saved transport is still there
    assert edit(tmp_path, json.loads(path.read_text())["mcpServers"]["s"],
                {"kind": "url", "url": "https://other.example/mcp"}) == {"transport": "sse", "timeout": 5,
                                                                         "url": "https://other.example/mcp"}


def test_an_env_or_argument_edit_needs_the_other_saved_values_again(tmp_path):
    cmd = {"command": "uvx", "args": ["", " left ", "a b"], "env": {"K": "old"}}
    path = tmp_path / "mcp.json"
    mcp_settings.save(path, "s", cmd)
    visible = mcp_settings.describe(path)["servers"]["s"]
    [f] = call(["fromConfig", visible])
    arg, env = call(["toConfig", {**f, "args": f["args"] + "\n--debug"}, visible],
                    ["toConfig", {**f, "env": "K=again\nM=new"}, visible])
    assert arg["config"]["args"] == ["", " left ", "a b", "--debug"]
    with pytest.raises(UIError) as e:
        mcp_settings.save(path, "s", arg["config"])
    assert e.value.params["keys"] == "K"
    mcp_settings.save(path, "s", env["config"])
    assert json.loads(path.read_text())["mcpServers"]["s"]["env"] == {"K": "again", "M": "new"}



def test_a_save_cannot_confirm_a_saved_secret(tmp_path):
    path = tmp_path / "mcp.json"
    saved = {"url": "https://m.example/mcp", "headers": {"A": "secret-a", "B": "secret-b"}}
    # guessing A right or wrong while B is left blank gives the same answer
    for guess in ("secret-a", "wrong"):
        assert resave(path, saved, {**saved, "headers": {"A": guess, "B": ""}}) == "B", guess


def test_values_typed_for_the_other_type_are_not_stored():
    url = {"url": "https://m.example/mcp"}
    [f] = call(["fromConfig", url])
    [r] = call(["toConfig", {**f, "token": "typed", "kind": "command", "command": "npx"}, url])
    assert "headers" not in r["config"], r
    cmd = {"command": "npx", "env": {"K": "", "L": ""}}
    [f] = call(["fromConfig", cmd])
    [r] = call(["toConfig", {**f, "env": "K=typed\nN=new", "kind": "url", "url": "https://m.example/mcp"}, cmd])
    assert r["config"]["env"] == {"K": ""}


def test_arguments_typed_for_a_command_are_not_stored_with_a_url():
    cmd = {"command": "npx", "args": ["a"]}
    [f] = call(["fromConfig", cmd])
    typed, removed = call(["toConfig", {**f, "args": "a\n--key=secret", "kind": "url", "url": "https://m.example/mcp"}, cmd],
                          ["toConfig", {**f, "args": "", "kind": "url", "url": "https://m.example/mcp"}, cmd])
    assert typed["config"]["args"] == ["a"] and "args" not in removed["config"]


def test_the_clear_token_switch_is_outside_the_url_fields():
    html = (JS.parents[1] / "index.html").read_text(encoding="utf-8")
    url_fields = html[html.index('<div id="mcp-url-fields">'):html.index('<div id="mcp-command-fields">')]
    assert 'id="mcp-token-clear-row"' in html and 'id="mcp-token-clear-row"' not in url_fields


def test_reordered_header_names_ask_for_the_secrets_again(tmp_path):
    saved = {"url": "https://m.example/mcp", "headers": {"X-Route": "a", "x-route": "b"}}
    assert resave(tmp_path / "mcp.json", saved, {**saved, "headers": {"x-route": "", "X-Route": ""}}) == "x-route, X-Route"
