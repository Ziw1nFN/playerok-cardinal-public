from __future__ import annotations

import copy
import logging
import os
import shutil
from datetime import datetime

from accounts import AccountRegistry, env_keys
from Utils import config as cfg
from Utils.envfile import comment_env, read_env, set_env
from Utils.settings import DEFAULT_SETTINGS, save_settings
from Utils.storage import AccountStorage

logger = logging.getLogger("cardinal.migration")

V1_ENV = {
    "token": "PLAYEROK_TOKEN",
    "cookies": "PLAYEROK_COOKIES",
    "ddg5": "PLAYEROK_DDG5",
    "proxy": "PLAYEROK_PROXY",
    "user_agent": "PLAYEROK_USER_AGENT",
}

TEXTS = {"greetingsText": "greeting", "reviewThanksText": "review_thanks", "watermark": "watermark"}
MOVED_CACHES = ("greeted.json", "delivered_deals.json", "raise.json")


def needs_migration(root: str = ".") -> bool:
    if os.path.exists(os.path.join(root, "accounts.json")):
        return False
    env = read_env(os.path.join(root, ".env"))
    return bool(env.get("PLAYEROK_TOKEN") or env.get("PLAYEROK_COOKIES"))


def _number(value: str):
    number = float(value)
    return int(number) if number.is_integer() else number


def settings_from_v1(main_cfg, ar_cfg, ad_cfg) -> dict:
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if main_cfg is not None:
        if main_cfg.has_section("Playerok"):
            section = main_cfg["Playerok"]
            for key in settings["toggles"]:
                if key in section:
                    settings["toggles"][key] = section[key] == "1"
            for key, target in TEXTS.items():
                if key in section:
                    settings["texts"][target] = section[key]
            for key in settings["numbers"]:
                if key in section:
                    settings["numbers"][key] = _number(section[key])
            settings["raise_items"] = section.get("raiseItems", "")
        if main_cfg.has_section("Notifications"):
            for key in settings["notifications"]:
                if key in main_cfg["Notifications"]:
                    settings["notifications"][key] = main_cfg["Notifications"][key] == "1"
    if ar_cfg is not None:
        for index, section in enumerate(ar_cfg.sections(), 1):
            settings["auto_responses"].append({
                "id": f"r{index}",
                "commands": cfg.split_commands(section),
                "text": ar_cfg.get(section, "response"),
                "notify": ar_cfg.get(section, "notify", fallback="0") == "1",
            })
    if ad_cfg is not None:
        for index, section in enumerate(ad_cfg.sections(), 1):
            settings["auto_delivery"].append({
                "id": f"d{index}",
                "item": section,
                "text": ad_cfg.get(section, "response"),
                "products_file": ad_cfg.get(section, "productsFile", fallback="").strip(),
                "enabled": ad_cfg.get(section, "disable", fallback="0") != "1",
            })
    return settings


def _backup(root: str) -> str:
    target = os.path.join(root, "backup", f"v1-{datetime.now():%Y%m%d-%H%M%S}")
    os.makedirs(target, exist_ok=True)
    for folder in ("configs", "storage"):
        source = os.path.join(root, folder)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(target, folder))
    if os.path.exists(os.path.join(root, ".env")):
        shutil.copy2(os.path.join(root, ".env"), os.path.join(target, ".env"))
    return target


def _load_v1_configs(root: str):
    paths = {name: os.path.join(root, path) for name, path in
             (("main", cfg.MAIN_CFG), ("ar", cfg.AR_CFG), ("ad", cfg.AD_CFG))}
    main = cfg.load_main(paths["main"]) if os.path.exists(paths["main"]) else None
    ar = cfg.load_auto_response(paths["ar"]) if os.path.exists(paths["ar"]) else None
    ad = cfg.load_auto_delivery(paths["ad"], os.path.join(root, "storage", "products")) \
        if os.path.exists(paths["ad"]) else None
    return main, ar, ad


def migrate_v1(root: str = ".") -> str:
    backup = _backup(root)
    env_path = os.path.join(root, ".env")
    env = read_env(env_path)
    registry = AccountRegistry(os.path.join(root, "accounts.json"))
    info = registry.add("")
    keys = env_keys(info.id)
    set_env({keys[field]: env[old] for field, old in V1_ENV.items() if env.get(old)}, env_path)
    comment_env([old for old in V1_ENV.values() if old in env], env_path)

    settings = settings_from_v1(*_load_v1_configs(root))
    old_cache = AccountStorage(os.path.join(root, "storage"))
    for uuid in old_cache.load_cache("disabled_plugins.json", []):
        settings["plugins"][uuid] = False
    storage = AccountStorage(registry.folder(info.id))
    save_settings(storage.settings_path, settings)

    os.makedirs(storage.products_dir, exist_ok=True)
    for name in old_cache.product_files():
        os.replace(old_cache.products_path(name), storage.products_path(name))
    os.makedirs(storage.cache_dir, exist_ok=True)
    for name in MOVED_CACHES:
        source = os.path.join(old_cache.cache_dir, name)
        if os.path.exists(source):
            os.replace(source, os.path.join(storage.cache_dir, name))
    logger.info(f"Данные v1 перенесены в аккаунт {info.id}, копия: {backup}")
    return info.id
