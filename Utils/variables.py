from __future__ import annotations

import re
from datetime import datetime

from Utils.tools import chat_link, deal_link

VARIABLES = {
    "nickname": "ник покупателя",
    "item": "название товара",
    "price": "цена товара",
    "order": "ссылка на сделку",
    "order_id": "номер сделки",
    "chat": "ссылка на чат с покупателем",
    "product": "товар со склада (код, ключ, данные)",
    "seller": "ник вашего аккаунта",
    "date": "сегодняшняя дата",
    "time": "текущее время",
}

TEXT_KINDS = {
    "greeting": ["nickname", "seller", "date", "time"],
    "review_thanks": ["nickname", "item", "price", "order", "order_id", "seller", "date", "time"],
    "auto_response": ["nickname", "chat", "seller", "date", "time"],
    "delivery": list(VARIABLES),
    "watermark": ["seller", "date", "time"],
    "deal_reply": ["nickname", "item", "price", "order", "order_id", "chat", "seller", "date", "time"],
    "reminder": ["nickname", "item", "price", "order", "order_id", "chat", "seller", "date", "time"],
    "template": ["nickname", "chat", "seller", "date", "time"],
}

LEGACY = {
    "username": "nickname",
    "item_name": "item",
    "price": "price",
    "deal_id": "order_id",
    "deal_link": "order",
    "product": "product",
    "date": "date",
    "time": "time",
}

_PATTERN = re.compile(r"#([a-z_]{2,})")
_TOKENS = re.compile("#(" + "|".join(sorted(VARIABLES, key=len, reverse=True)) + r")|\$("
                     + "|".join(sorted(LEGACY, key=len, reverse=True)) + ")")


def _prepared(values: dict[str, object]) -> dict[str, str]:
    result = {key: "" if value is None else str(value) for key, value in values.items()}
    now = datetime.now()
    result.setdefault("date", now.strftime("%d.%m.%Y"))
    result.setdefault("time", now.strftime("%H:%M"))
    if result.get("order_id") and not result.get("order"):
        result["order"] = deal_link(result["order_id"])
    if result.get("chat_id") and not result.get("chat"):
        result["chat"] = chat_link(result["chat_id"])
    return result


def render(text: str, values: dict[str, object]) -> str:
    prepared = _prepared(values)
    return _TOKENS.sub(lambda m: prepared.get(m.group(1) or LEGACY[m.group(2)], ""), text)


def unknown_variables(text: str, kind: str) -> list[str]:
    allowed = set(TEXT_KINDS.get(kind, []))
    found = []
    for name in _PATTERN.findall(text or ""):
        if name not in allowed and name not in found:
            found.append(name)
    return found


def sample_values() -> dict[str, str]:
    return {
        "nickname": "Покупатель123",
        "item": "500 робуксов",
        "price": "499",
        "order_id": "1f0a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8",
        "chat_id": "1f0d9e8c-7b6a-5f4e-3d2c-1b0a99887766",
        "product": "ABCD-EFGH",
        "seller": "МойМагазин",
    }
