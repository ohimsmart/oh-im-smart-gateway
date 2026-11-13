"""後台訊息的 key：msg_key 是帶 {佔位符} 的简中原文，頁面用它查字典再填參數（spec §6.1）。

參數值是普通字串時，頁面一字不改地顯示（名稱、設備認的口令、第三方錯誤原文）；
我們自己的詞語或 catalog 標籤要翻譯時，用 label() 或 msg() 包成物件傳入（句中嵌句也是這樣）。
"""
import re

_FIELD = re.compile(r"\{(\w+)\}")


def msg(key: str, **params) -> dict:
    return {"msg_key": key, "msg_params": params}


def label(text) -> dict:
    """我們自己的詞語或 catalog 標籤作參數時用：頁面會翻譯它。普通字串參數原樣顯示。"""
    return {"msg_key": str(text), "msg_params": {}}


def render(value) -> str:
    """填好參數的简中句子，給舊欄位（message、error、note）和日誌用。"""
    if isinstance(value, dict) and isinstance(value.get("msg_key"), str):
        params = value.get("msg_params") or {}
        return _FIELD.sub(lambda m: render(params[m.group(1)]) if m.group(1) in params else m.group(0),
                          value["msg_key"])
    return "" if value is None else str(value)


class KeyedError(Exception):
    """帶 key 的例外；str(e) 是填好參數的简中句子。"""

    def __init__(self, key: str, **params):
        self.key, self.params = key, params
        super().__init__(render(msg(key, **params)))

    def payload(self) -> dict:
        return msg(self.key, **self.params)


class UIError(KeyedError, ValueError):
    """顯示給後台用戶的驗證錯誤。是 ValueError 的子類，現有的 except ValueError 照樣接得到。"""
