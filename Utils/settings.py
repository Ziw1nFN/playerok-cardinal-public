from __future__ import annotations

import copy
import json
import os

from Utils.config import is_safe_filename

DEFAULT_SETTINGS = {
    "version": 2,
    "toggles": {
        "autoResponse": True,
        "autoDelivery": True,
        "autoSent": True,
        "greetings": True,
        "reviewThanks": False,
        "autoRestoreSold": False,
        "autoRestoreExpired": True,
        "autoRaise": False,
        "dealButtons": False,
    },
    "texts": {
        "greeting": "Привет, #nickname! 👋 Чем могу помочь?",
        "review_thanks": "Спасибо за отзыв, #nickname! 💙",
        "watermark": "",
    },
    "numbers": {
        "greetingsCooldown": 2,
        "restoreCheckInterval": 10,
        "raiseInterval": 60,
        "raiseMaxPrice": 30,
        "raiseDailyLimit": 100,
    },
    "raise_items": "",
    "notifications": {key: True for key in (
        "newMessage", "newDeal", "delivery", "review", "dealConfirmed",
        "dealProblem", "dealRolledBack", "autoResponse", "raise", "restore",
    )},
    "auto_responses": [],
    "auto_delivery": [],
    "plugins": {},
    "deal_replies": {
        "confirmed": {"enabled": False, "text": "Спасибо за подтверждение, #nickname! Будем рады отзыву ⭐"},
        "problem": {"enabled": False, "text": "Видим вашу проблему по заказу, скоро разберёмся 🙏"},
        "rolled_back": {"enabled": False, "text": "Деньги по заказу возвращены, #nickname"},
        "sent": {"enabled": False, "text": "Заказ выполнен ✅ Проверьте и подтвердите, пожалуйста"},
    },
    "reminder": {"enabled": False, "hours": 12, "text": "#nickname, подтвердите, пожалуйста, заказ #order 🙏"},
    "chat_commands": {"enabled": True, "complete": ["!выполнить", "!готово"], "refund": ["!вернуть", "!возврат"]},
    "templates": [
        {"id": "t1", "title": "👋 Привет", "text": "Здравствуйте, #nickname! Сейчас всё сделаю 🙌"},
        {"id": "t2", "title": "⏳ Скоро выдам", "text": "#nickname, заказ в работе, выдам в течение нескольких минут ⏳"},
        {"id": "t3", "title": "✅ Готово, подтвердите", "text": "Готово ✅ Проверьте и подтвердите заказ, пожалуйста 🙏"},
    ],
    "chat_sync": {"enabled": True, "group_id": None, "group_title": "", "duplicate_private": False, "link_buyer": False, "paused": ""},
    "profiles": [],
    "withdraw": {"enabled": False, "mode": "threshold", "threshold": 1000, "keep": 0, "time": "12:00",
                 "daily_limit": 50000, "verified": False, "paused": ""},
}

DEAL_REPLY_KINDS = ("confirmed", "problem", "rolled_back", "sent")
REMINDER_HOURS = (1, 168)

NUMBER_LIMITS = {
    "greetingsCooldown": (0, 365),
    "restoreCheckInterval": (1, 1440),
    "raiseInterval": (5, 10080),
    "raiseMaxPrice": (0, 100000),
    "raiseDailyLimit": (0, 1000000),
}


class SettingsError(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _merge(default, data, path: str):
    if isinstance(default, dict):
        if not isinstance(data, dict):
            raise SettingsError(f"{path}: ожидался словарь")
        result = {key: _merge(value, data[key], f"{path}.{key}") if key in data else copy.deepcopy(value)
                  for key, value in default.items()}
        for key, value in data.items():
            result.setdefault(key, value)
        return result
    if isinstance(default, bool):
        if not isinstance(data, bool):
            raise SettingsError(f"{path}: ожидалось true или false")
        return data
    if _is_number(default):
        if not _is_number(data):
            raise SettingsError(f"{path}: ожидалось число")
        return data
    if isinstance(default, str):
        if not isinstance(data, str):
            raise SettingsError(f"{path}: ожидалась строка")
        return data
    if isinstance(default, list):
        if not isinstance(data, list):
            raise SettingsError(f"{path}: ожидался список")
        return data
    return data


def save_settings(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_settings(path: str) -> dict:
    if not os.path.exists(path):
        data = copy.deepcopy(DEFAULT_SETTINGS)
        save_settings(path, data)
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise SettingsError(f"Файл настроек повреждён: {e}")
    merged = _merge(DEFAULT_SETTINGS, raw, "settings")
    if merged != raw:
        save_settings(path, merged)
    return merged


def validate_delivery(entry: dict) -> str | None:
    text = (entry.get("text") or "").strip()
    products_file = (entry.get("products_file") or "").strip()
    if not text:
        return "Текст выдачи пустой"
    if products_file and not is_safe_filename(products_file, ".txt"):
        return "Файл склада должен быть именем .txt без папок"
    has_product = "#product" in text or "$product" in text
    if has_product and not products_file:
        return "В тексте есть #product, но не выбран склад"
    if products_file and not has_product:
        return "Есть склад, но в тексте нет #product — покупатель не получит товар"
    return None


def new_id(items: list[dict], prefix: str) -> str:
    numbers = [int(item["id"][len(prefix):]) for item in items
               if str(item.get("id", "")).startswith(prefix) and str(item["id"])[len(prefix):].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1}"
