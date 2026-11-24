from __future__ import annotations

from datetime import datetime

from Utils.storage import GLOBAL

PRODUCTS_DIR = GLOBAL.products_dir
CACHE_DIR = GLOBAL.cache_dir


def deal_link(deal_id: str) -> str:
    return f"https://playerok.com/deal/{deal_id}"


def chat_link(chat_id: str) -> str:
    return f"https://playerok.com/chats/{chat_id}"


def item_link(slug: str) -> str:
    return f"https://playerok.com/products/{slug}"


def profile_link(username: str) -> str:
    return f"https://playerok.com/profile/{username}"


def format_text(text: str, **variables) -> str:
    now = datetime.now()
    values = {
        "date": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M"),
    }
    values.update({k: "" if v is None else str(v) for k, v in variables.items()})
    if values.get("deal_id") and "deal_link" not in variables:
        values["deal_link"] = deal_link(values["deal_id"])
    for key in sorted(values, key=len, reverse=True):
        text = text.replace(f"${key}", values[key])
    return text


def products_path(filename: str) -> str:
    return GLOBAL.products_path(filename)


def read_products(filename: str) -> list[str]:
    return GLOBAL.read_products(filename)


def write_products(filename: str, products: list[str]):
    GLOBAL.write_products(filename, products)


def take_product(filename: str) -> str | None:
    return GLOBAL.take_product(filename)


def return_product(filename: str, product: str):
    GLOBAL.return_product(filename, product)


def count_products(filename: str) -> int:
    return GLOBAL.count_products(filename)


def load_cache(name: str, default):
    return GLOBAL.load_cache(name, default)


def save_cache(name: str, data):
    GLOBAL.save_cache(name, data)


def short(text: str | None, limit: int = 40) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit - 1] + "…"
