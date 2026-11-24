from __future__ import annotations

import configparser
import os

MAIN_CFG = "configs/_main.cfg"
AR_CFG = "configs/auto_response.cfg"
AD_CFG = "configs/auto_delivery.cfg"

MAIN_DEFAULTS = {
    "Playerok": {
        "autoResponse": "1",
        "autoDelivery": "1",
        "autoSent": "1",
        "greetings": "1",
        "greetingsText": "Привет, $username! 👋 Чем могу помочь?",
        "greetingsCooldown": "2",
        "reviewThanks": "0",
        "reviewThanksText": "Спасибо за отзыв, $username! 💙",
        "autoRestoreSold": "0",
        "autoRestoreExpired": "1",
        "restoreCheckInterval": "10",
        "autoRaise": "0",
        "raiseInterval": "60",
        "raiseMaxPrice": "30",
        "raiseDailyLimit": "100",
        "raiseItems": "",
        "watermark": "",
    },
    "Notifications": {
        "newMessage": "1",
        "newDeal": "1",
        "delivery": "1",
        "review": "1",
        "dealConfirmed": "1",
        "dealProblem": "1",
        "dealRolledBack": "1",
        "autoResponse": "1",
        "raise": "1",
        "restore": "1",
    },
}

AR_TEMPLATE = """# Автоответчик. Название секции — команда покупателя (регистр не важен).
# Несколько команд через |, например [!помощь | !help]
# response — ответ; notify = 1 — прислать уведомление в Telegram.
# Переменные: $username, $date, $time

[!помощь | !help]
response = 👋 Привет, $username!
  Я бот-помощник продавца. Продавец скоро ответит.
notify = 1
"""

AD_TEMPLATE = """# Автовыдача. Название секции — точное название товара на Playerok (регистр не важен).
# response — сообщение покупателю после оплаты.
# productsFile — файл с товарами в storage/products (одна строка = один товар), подставляется в $product.
# disable = 1 — временно выключить выдачу для этого товара.
# Переменные: $username, $item_name, $price, $deal_id, $deal_link, $product, $date, $time
#
# [Пример товара]
# response = Спасибо за покупку, $username! 🎉
#   Ваш товар: $product
#   Подтвердите, пожалуйста, сделку и оставьте отзыв ⭐
# productsFile = example.txt
"""


class ConfigError(Exception):
    def __init__(self, path: str, text: str):
        super().__init__(f"{path}: {text}")
        self.path = path
        self.text = text


def new_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",), comment_prefixes=("#", ";"),
                                       inline_comment_prefixes=None, strict=False)
    parser.optionxform = str
    return parser


def read_file(path: str) -> configparser.ConfigParser:
    parser = new_parser()
    try:
        with open(path, "r", encoding="utf-8") as f:
            parser.read_file(f)
    except configparser.Error as e:
        raise ConfigError(path, f"ошибка синтаксиса: {e}")
    return parser


def save(parser: configparser.ConfigParser, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        parser.write(f)
    os.replace(tmp, path)


def ensure_files():
    for folder in ("configs", "storage/products", "storage/cache", "plugins", "logs"):
        os.makedirs(folder, exist_ok=True)
    if not os.path.exists(AR_CFG):
        with open(AR_CFG, "w", encoding="utf-8") as f:
            f.write(AR_TEMPLATE)
    if not os.path.exists(AD_CFG):
        with open(AD_CFG, "w", encoding="utf-8") as f:
            f.write(AD_TEMPLATE)


def load_main(path: str = MAIN_CFG) -> configparser.ConfigParser:
    parser = read_file(path) if os.path.exists(path) else new_parser()
    changed = not os.path.exists(path)
    for section, values in MAIN_DEFAULTS.items():
        if not parser.has_section(section):
            parser.add_section(section)
            changed = True
        for key, value in values.items():
            if not parser.has_option(section, key):
                parser.set(section, key, value)
                changed = True
    for section, values in MAIN_DEFAULTS.items():
        for key, value in values.items():
            if value in ("0", "1") and parser.get(section, key) not in ("0", "1"):
                raise ConfigError(path, f"[{section}] {key} должен быть 0 или 1")
    for key in ("greetingsCooldown", "restoreCheckInterval", "raiseInterval", "raiseMaxPrice", "raiseDailyLimit"):
        try:
            if float(parser.get("Playerok", key)) < 0:
                raise ValueError
        except ValueError:
            raise ConfigError(path, f"[Playerok] {key} должен быть неотрицательным числом")
    if changed:
        save(parser, path)
    return parser


def split_commands(section: str) -> list[str]:
    return [c.strip().lower() for c in section.split("|") if c.strip()]


def load_auto_response(path: str = AR_CFG) -> configparser.ConfigParser:
    parser = read_file(path) if os.path.exists(path) else new_parser()
    seen = {}
    for section in parser.sections():
        if not parser.get(section, "response", fallback="").strip():
            raise ConfigError(path, f"в секции [{section}] нет параметра response")
        if parser.get(section, "notify", fallback="0") not in ("0", "1"):
            raise ConfigError(path, f"[{section}] notify должен быть 0 или 1")
        for command in split_commands(section):
            if command in seen:
                raise ConfigError(path, f"команда «{command}» повторяется в [{seen[command]}] и [{section}]")
            seen[command] = section
    return parser


def load_auto_delivery(path: str = AD_CFG, products_dir: str = "storage/products") -> configparser.ConfigParser:
    parser = read_file(path) if os.path.exists(path) else new_parser()
    names = {}
    for section in parser.sections():
        key = section.strip().lower()
        if key in names:
            raise ConfigError(path, f"товар [{section}] указан дважды")
        names[key] = section
        response = parser.get(section, "response", fallback="").strip()
        if not response:
            raise ConfigError(path, f"в секции [{section}] нет параметра response")
        if parser.get(section, "disable", fallback="0") not in ("0", "1"):
            raise ConfigError(path, f"[{section}] disable должен быть 0 или 1")
        products_file = parser.get(section, "productsFile", fallback="").strip()
        if products_file:
            if "$product" not in response:
                raise ConfigError(path, f"[{section}] указан productsFile, но в response нет $product")
            if not is_safe_filename(products_file, ".txt"):
                raise ConfigError(path, f"[{section}] productsFile должен быть именем .txt файла без папок")
            full = os.path.join(products_dir, products_file)
            if not os.path.exists(full):
                os.makedirs(products_dir, exist_ok=True)
                open(full, "w", encoding="utf-8").close()
        elif "$product" in response:
            raise ConfigError(path, f"[{section}] в response есть $product, но не указан productsFile")
    return parser


def is_safe_filename(name: str, extension: str) -> bool:
    return (
        bool(name)
        and name == os.path.basename(name)
        and name.lower().endswith(extension)
        and name not in (".", "..")
        and not any(ch in name for ch in '\\/:*?"<>|')
    )
