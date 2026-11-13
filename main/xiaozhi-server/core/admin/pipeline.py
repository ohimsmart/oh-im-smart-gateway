"""管理后台"工作原理"页：一句话经过的五步、"怎么跟它说话"速查，以及发给大模型的提示词。

面向正在用设备的人，只讲"说了什么会怎样"；为什么这样设计、每一步的实现细节在 hub-docs/design.md。
只读：不修改配置，也不影响运行中的服务。页面上的值都从配置读取，切换模块后跟着变。
提示词按原项目的拼装方式（core/utils/prompt_manager.py、core/utils/dialogue.py）重新渲染一份预览，
模板变量用标记包起来，前端据此高亮显示"这里填进了什么"。
"""

import os
from datetime import datetime

from jinja2 import Template

from config.config_loader import get_project_dir
from core.admin import catalog, connections, pages
from core.admin.i18n_msg import label, msg

# 变量标记：\x01变量名\x02值\x03，前端换成高亮
MARK_START, MARK_SEP, MARK_END = "\x01", "\x02", "\x03"

VAR_LABELS = {
    "base_prompt": "人设",
    "current_time": "当前时间",
    "today_date": "日期",
    "today_weekday": "星期",
    "lunar_date": "农历",
    "emojiList": "表情列表",
    "dynamic_context": "动态上下文",
    "language": "回复语言",
}

def _fewshot_text() -> str:
    """和 connection._inject_tool_call_fewshot 注入的示例一致，按 dialogue.render_fewshot 写进系统提示的样子。"""
    from core.utils.dialogue import Message, render_fewshot

    def call(name, args):
        return Message(role="assistant", tool_calls=[{"function": {"name": name, "arguments": args}}])

    return render_fewshot([
        Message(role="user", content="给我讲个故事吧"),
        call("direct_answer", '{"response": "好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一个我给你开讲~"}'),
        Message(role="tool", content="已直接回复"),
        Message(role="user", content="拜拜"),
        call("handle_exit_intent", '{"say_goodbye": "再见，下次再聊~"}'),
        Message(role="tool", content="退出意图已处理"),
        Message(role="assistant", content="再见，下次再聊~"),
    ])


def _mark(name: str, value) -> str:
    return f"{MARK_START}{name}{MARK_SEP}{value}{MARK_END}"


def _entry(merged: dict, section: str) -> tuple:
    name = (merged.get("selected_module") or {}).get(section)
    entry = (merged.get(section) or {}).get(name) or {}
    return name, entry if isinstance(entry, dict) else {}


def _online_conn():
    """任意一台在线设备的连接（用来取它实际使用的提示词）。"""
    for item in connections.snapshot():
        conn = connections.get(item["session_id"])
        if conn is not None:
            return conn
    return None


def _system_prompt_of(conn) -> str:
    dialogue = getattr(getattr(conn, "dialogue", None), "dialogue", None) or []
    for m in dialogue:
        if getattr(m, "role", None) == "system":
            return str(getattr(m, "content", "") or "")
    return ""


# ---------- 流程 ----------

DESIGN_DOC_URL = "https://github.com/ohimsmart/oh-im-smart-gateway/blob/main/hub-docs/design.md"

def _raw(value):
    """配置里的名称（条目名、model_name、type）：页面原样显示，不会碰巧被当成字典的键翻译。"""
    return msg("{value}", value=value)


def _voice_name(voice: str):
    info = next((v for v in catalog.EDGE_VOICES if v["value"] == voice), None)
    return msg("{name}（{group}）", name=label(info["name"]), group=label(info["group"])) if info else voice


def _llm_label(name: str, entry: dict):
    if entry.get("type") == "claude_cli":
        return "Claude Code"
    vendor = catalog.VENDOR_BY_ENTRY.get(name)
    return label(vendor["label"]) if vendor else _raw(name)


def _asr_label(name: str, entry: dict):
    if entry.get("type") == "doubao_stream":
        return msg("豆包流式识别")
    if entry.get("type") == "sherpa_onnx_local":
        if "x-asr" in str(entry.get("model_dir") or "").lower():
            return msg("本地 X-ASR")
        model_type = entry.get("model_type") or "sense_voice"
        return msg("本地 {model}", model=label(pages.SHERPA_MODEL_LABELS.get(model_type, model_type)))
    return _raw(name) if name else msg("未设置")


def _claude_model_label(model):
    option = next((o for o in catalog.CLAUDE_MODELS if o["value"] == model), None)
    return label(option["label"]) if option else (str(model) if model else msg("默认模型"))


def _effort_label(effort):
    option = next((o for o in catalog.CLAUDE_EFFORTS if o["value"] == effort), None)
    return label(option["label"]) if option else msg("默认")


def describe_stages(merged: dict) -> list:
    """一句话经过的五步：每步只给当前用的模块和一个关键设置，点击跳到对应的设置页（page 为 None 表示后台没有设置页）。"""
    _, vad = _entry(merged, "VAD")
    asr_name, asr = _entry(merged, "ASR")
    llm_name, llm = _entry(merged, "LLM")
    tts_name, tts = _entry(merged, "TTS")
    silence = vad.get("min_silence_duration_ms", 1000)
    prebuffer = int(merged.get("tts_audio_prebuffer_count") or 5)

    if llm.get("type") == "claude_cli":
        llm_value = msg("Claude Code · {model}", model=_claude_model_label(llm.get("model")))
        llm_hint = msg("思考力度：{effort}", effort=_effort_label(llm.get("effort")))
    else:
        llm_value = _llm_label(llm_name, llm)
        llm_hint = _raw(str(llm.get("model_name") or ""))
    if tts.get("type") == "edge":
        tts_value = msg("EdgeTTS · {voice}", voice=_voice_name(str(tts.get("voice") or "")))
        tts_hint = "微软在线语音，需要联网"
    else:
        tts_value, tts_hint = _raw(tts_name) if tts_name else msg("未设置"), _raw(str(tts.get("type") or ""))
    local_asr = asr.get("type") == "sherpa_onnx_local"
    return [
        {"id": "listen", "icon": "🎙", "title": "收音", "value": msg("停顿 {ms}ms 算说完", ms=silence),
         "hint": "说话中间停顿太久会被截成两句", "page": "asr"},
        {"id": "asr", "icon": "📝", "title": "识别", "value": _asr_label(asr_name, asr),
         "hint": "在本机识别，不联网" if local_asr else "云端识别，需要联网", "page": "asr"},
        {"id": "llm", "icon": "✦", "title": "大模型", "value": llm_value, "hint": llm_hint, "page": "llm"},
        {"id": "tts", "icon": "🔊", "title": "合成", "value": tts_value, "hint": tts_hint, "page": "tts"},
        {"id": "play", "icon": "📢", "title": "播放", "value": msg("缓冲约 {s} 秒", s=f"{prebuffer * 60 / 1000:.1f}"),
         "hint": "说话断续时，在配置文件里调大", "page": None},
    ]


def _duration(seconds):
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return msg("{n} 小时", n=minutes // 60) if minutes % 60 == 0 else msg("{n} 分钟", n=minutes)
    return msg("{n} 秒", n=int(seconds))


def _setting(entry: dict, key: str, default: float) -> float:
    """和 claude_cli 读配置的规则一样：没填（None 或空字符串）用默认值，填 0 就是 0。"""
    from core.providers.llm.claude_cli.claude_cli import _num

    return _num(entry, key, default)


def _quoted(words):
    """“甲”“乙”：全是用户填的词，没有要翻译的字。"""
    return "".join(f"“{w}”" for w in words)


def _either(words):
    """接上次对话的关键词，最多两个：“甲”或“乙”。"""
    words = [str(w) for w in words[:2]]
    return msg("“{a}”或“{b}”", a=words[0], b=words[1]) if len(words) == 2 else _quoted(words)


def _join(items, sep):
    """把多项接起来：分隔符是我们的标点，交给 msg 翻译；每项原样（字符串）或可翻译（msg 物件）。

    对半折叠，嵌套深度是 log n：称呼没有数量上限，逐项折叠会让 JSON 和页面递归太深。
    """
    if len(items) <= 1:
        return items[0] if items else ""
    a, b = _join(items[:len(items) // 2], sep), _join(items[len(items) // 2:], sep)
    return msg("{a}；{b}", a=a, b=b) if sep == "；" else msg("{a}、{b}", a=a, b=b)


def describe_tips(merged: dict) -> list:
    """"你这样说 → 会这样"的速查卡片，值都从当前配置读取。"""
    calls = pages.describe_call_names(merged)
    llms = merged.get("LLM") or {}
    default_name = (merged.get("selected_module") or {}).get("LLM")
    called = [m["entry"] for m in calls["models"]] if calls["enabled"] else []
    names = [default_name, *called]
    cli = next((llms[n] for n in names if isinstance(llms.get(n), dict) and llms[n].get("type") == "claude_cli"), None)
    has_api = any(isinstance(llms.get(n), dict) and llms[n].get("type") != "claude_cli" for n in names)

    # 引号里设备认的口令（唤醒词、停下、继续、退出词、接续关键词、应答语）都是普通字符串参数，原样显示；
    # “嗨”留在范本里：呼名也认 hi/hey（core/agent/call_names.py），英文页面可以译成 Hi
    tips = [{"title": "唤醒", "say": msg("说唤醒词（StackChan 是“{wake}”），或按一下按键、点一下屏幕", wake="Hi, StackChan"),
             "result": msg("设备开始听你说话。交给这台设备上次用的模型，第一次用时是 {model}",
                           model=_llm_label(default_name, llms.get(default_name) or {}))}]
    if calls["enabled"]:
        # describe_call_names 找不到对应的模型时，标签就是条目名本身（配置里的值），原样显示
        aliases = _join([msg("{label}：{aliases}", label=m.get("label_msg") or (
                             _raw(m["label"]) if m["label"] == m["entry"] else label(m["label"])),
                             aliases=_join(list(m["aliases"]), "、")) for m in calls["models"]], "；")
        if aliases:
            result = msg("换成对应的模型回答，之后一直用它；只说称呼时回一声“{reply}”。现在的称呼 —— {aliases}",
                         reply=calls["reply"], aliases=aliases)
        else:
            result = msg("换成对应的模型回答，之后一直用它；只说称呼时回一声“{reply}”。还没有配置称呼", reply=calls["reply"])
        tips.append({"title": "换模型", "say": "句首说“嗨 + 称呼”，后面可以直接接内容", "result": result})
    tips.append({"title": "打断", "say": "设备说话时按一下按键（StackChan 是点一下屏幕上的脸），或者喊唤醒词",
                 "result": "马上停下，然后可以说下一句。设备说话时不收音，不能直接插话"})
    if cli:
        progress = _setting(cli, "progress_interval_seconds", 30)
        turn = _setting(cli, "turn_timeout_seconds", 600)
        if not turn:
            wait = msg("设备陪着等，不限时长")
        else:
            wait = msg("设备陪着等，最长 {d}", d=_duration(turn))
        nudge = msg("；{d}没开口就提示一次进度", d=_duration(progress)) if progress else ""
        # 停下、继续是设备认的口令（core/agent/voice_commands.py 只认中文），作为普通字符串参数原样显示
        if _setting(cli, "background_max_seconds", 7200):
            after = msg("。中途打断、结束或等满了，Claude 不停，后台接着做，做完了告诉你；想让它停下就说“{cmd}”", cmd="停下")
        else:
            after = msg("。被停下或打断后，说“{cmd}”接着做", cmd="继续")
        tips.append({"title": "长任务", "say": "让 Claude 做要几分钟的事",
                     "result": msg("{wait}{nudge}{after}", wait=wait, nudge=nudge, after=after)})
        window = _setting(cli, "resume_window_minutes", 120)
        keywords = cli.get("resume_keywords") or ["接着上次聊"]
        said = _either(keywords)
        if window:
            result = msg("{d}内再唤醒，Claude 自动接上上次的对话；隔得更久的，说{said}也能接上",
                         d=_duration(window * 60), said=said)
        else:
            result = msg("说{said}接上上次的对话", said=said)
        if has_api:
            result = msg("{text}。API 模型断开后就不记得了", text=result)
        tips.append({"title": "接着上次聊", "say": "过一会儿再唤醒设备", "result": result})
    elif has_api:
        tips.append({"title": "接着上次聊", "say": "过一会儿再唤醒设备",
                     "result": "API 模型只记得这次连接里的对话，断开后从头开始"})

    first = int(merged.get("close_connection_no_voice_time", 120) or 120)
    end_prompt = merged.get("end_prompt") or {}
    if end_prompt.get("enable", True) is False:
        farewell = msg("直接断开")
    elif end_prompt.get("reply"):
        farewell = msg("说一句“{reply}”后断开", reply=end_prompt["reply"])
    else:
        farewell = msg("说一句告别语后断开")
    exits = merged.get("exit_commands") or []
    if exits:
        result = msg("立即断开。{d}没人说话时，{farewell}", d=_duration(first), farewell=farewell)
    else:
        result = msg("{d}没人说话时，{farewell}", d=_duration(first), farewell=farewell)
    tips.append({"title": "结束", "say": msg("说{words}", words=_quoted(exits)) if exits else "不再说话",
                 "result": result})
    return tips


def describe_flow(merged: dict) -> dict:
    return {"stages": describe_stages(merged), "tips": describe_tips(merged)}


# ---------- 提示词拆解 ----------

def _template_name(merged: dict) -> str:
    return str(merged.get("prompt_template") or "hub-base-prompt.txt")


def _template_text(merged: dict) -> str:
    path = os.path.join(get_project_dir(), _template_name(merged))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _context_values(merged: dict) -> dict:
    from core.utils.current_time import get_current_date, get_current_lunar_date, get_current_weekday
    from core.utils.prompt_manager import EMOJI_List

    _, tts = _entry(merged, "TTS")
    return {
        "current_time": datetime.now().strftime("%H:%M"),
        "today_date": get_current_date(),
        "today_weekday": get_current_weekday(),
        "lunar_date": get_current_lunar_date(),
        "emojiList": EMOJI_List,
        "dynamic_context": "",
        "language": tts.get("language") or "中文",
    }


def render_base_prompt(merged: dict) -> str:
    """按服务端的方式渲染基础模板，变量值用标记包起来。"""
    template = _template_text(merged)
    persona = str(merged.get("prompt") or "")
    if not template:
        return _mark("base_prompt", persona)
    values = _context_values(merged)
    marked = {k: _mark(k, v) for k, v in values.items()}
    return Template(template).render(base_prompt=_mark("base_prompt", persona), emoji_enabled=True,
                                     device_id="", client_ip="", **marked)


def describe_prompt(merged: dict) -> dict:
    llm_name, llm = _entry(merged, "LLM")
    _, intent = _entry(merged, "Intent")
    is_cli = llm.get("type") == "claude_cli"
    conn = _online_conn()
    base = render_base_prompt(merged)

    segments = []
    if is_cli:
        segments.append({
            "id": "claude_preset", "title": "Claude Code 自带的系统提示", "source": "Claude Code", "when": "进程启动时",
            "text": "", "collapsed": True,
            "note": "Claude Code 内置的说明，介绍它自己有哪些工具、工作目录在哪、怎么回答编程问题等。"
                    "这段由 Claude Code 程序自己生成，不在我们的配置里，这里无法显示原文。我们的内容追加在它后面。",
        })
    segments.append({
        "id": "base", "title": "基础模板（含人设）", "source": "服务端", "when":
            "设备连接时生成；Claude 进程启动时固定" if is_cli else "设备连接时生成；当前时间每轮更新",
        "text": base, "collapsed": False,
        "note": msg("来自 {template}。高亮的部分是填进去的值：人设来自配置里的 prompt，日期、农历在设备连接时生成，"
                    "当前时间在 Claude 进程启动时取一次。", template=_template_name(merged)) if is_cli else
                msg("来自 {template}。高亮的部分是填进去的值：人设来自配置里的 prompt，日期、农历在设备连接时生成，"
                    "当前时间每轮发送前更新。", template=_template_name(merged)),
    })
    if is_cli:
        from core.providers.llm.claude_cli.claude_cli import VOICE_RULES

        segments.append({
            "id": "voice_rules", "title": "语音规则", "source": "服务端", "when": "进程启动时",
            "text": VOICE_RULES.strip(), "collapsed": False,
            "note": "我们为 Claude Code 加的规则：开头一个 emoji、简短口语、不用 Markdown。写在 claude_cli.py 的 VOICE_RULES。",
        })
        segments.append({
            "id": "turn", "title": "每轮发送的内容", "source": "服务端", "when": "每轮",
            "text": "（这一轮识别出的文字，例如：帮我看看工作目录里有哪些文件）", "collapsed": False,
            "note": "只发这一轮的话，之前的对话保存在 Claude Code 会话里。第一次识别到说话人时，会发 "
                    "{\"speaker\": \"名字\", \"content\": \"文字\"} 这样的 JSON（声纹识别目前没开）。",
        })
    else:
        if intent.get("type") == "function_call":
            segments.append({
                "id": "fewshot", "title": "工具调用示例", "source": "服务端", "when": "每轮",
                "text": _fewshot_text(), "collapsed": True,
                "note": "追加在系统提示词末尾的一段示例，教模型什么时候直接回答、什么时候调用工具。不会显示给用户。"
                        "原项目把它们当成几轮对话发，模型会当成用户真说过（问「我刚才让你做过什么」答「讲故事、说拜拜」），"
                        "所以改成写在系统提示里并注明不是真实对话。",
            })
            plugins = [p["label"] for p in catalog.PLUGINS if p["name"] in (intent.get("functions") or [])]
            segments.append({
                "id": "tools", "title": "工具清单", "source": "服务端", "when": "每轮",
                "text": "、".join(["直接回答（direct_answer）", *[p["label"] for p in catalog.ALWAYS_ON_PLUGINS], *plugins]),
                "collapsed": False,
                "note": "随请求一起发给模型的工具（OpenAI 协议的 tools 参数），每个工具带名字、用途和参数说明。",
            })
        segments.append({
            "id": "history", "title": "历史对话 + 这一轮的话", "source": "服务端", "when": "每轮",
            "text": "[user] 上一轮说的话\n[assistant] 上一轮的回复\n……\n[user] 这一轮识别出的文字", "collapsed": False,
            "note": "每轮都把这次连接以来的全部对话一起发送，没有截断；设备断开后清空（没有开启记忆）。",
        })

    warnings = []
    if is_cli:
        idle = _setting(llm, "idle_timeout_seconds", 300)
        if idle:  # 填 0 时每轮结束就关进程，下一轮重新传入，日期时间总是新的
            warnings.append(msg("提示词在 Claude Code 进程启动时一次性传入，进程存活期间（空闲 {d}才关闭）"
                                "里面的日期、时间不会更新。", d=_duration(idle)))

    actual = _system_prompt_of(conn) if conn is not None else ""
    return {
        "llm": _llm_label(llm_name, llm),
        "segments": segments,
        "warnings": warnings,
        "actual": {"device_id": getattr(conn, "device_id", None), "text": actual} if actual else None,
        "marks": {"start": MARK_START, "sep": MARK_SEP, "end": MARK_END},
        "var_labels": VAR_LABELS,
    }


def describe(merged: dict) -> dict:
    return {"flow": describe_flow(merged), "prompt": describe_prompt(merged), "design_url": DESIGN_DOC_URL}
