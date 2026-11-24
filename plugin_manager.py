from __future__ import annotations

import importlib.util
import logging
import os
import sys
from types import ModuleType
from typing import Callable
from uuid import UUID

from playerokapi.enums import EventTypes

logger = logging.getLogger("cardinal.plugins")

LIFECYCLE_BINDS = [
    "BIND_TO_PRE_INIT", "BIND_TO_POST_INIT",
    "BIND_TO_PRE_START", "BIND_TO_POST_START",
    "BIND_TO_PRE_STOP", "BIND_TO_POST_STOP",
]

EVENT_BINDS = {
    EventTypes.CHAT_INITIALIZED: "BIND_TO_CHAT_INITIALIZED",
    EventTypes.NEW_MESSAGE: "BIND_TO_NEW_MESSAGE",
    EventTypes.NEW_DEAL: "BIND_TO_NEW_DEAL",
    EventTypes.ITEM_PAID: "BIND_TO_ITEM_PAID",
    EventTypes.ITEM_SENT: "BIND_TO_ITEM_SENT",
    EventTypes.DEAL_CONFIRMED: "BIND_TO_DEAL_CONFIRMED",
    EventTypes.DEAL_CONFIRMED_AUTOMATICALLY: "BIND_TO_DEAL_CONFIRMED",
    EventTypes.DEAL_ROLLED_BACK: "BIND_TO_DEAL_ROLLED_BACK",
    EventTypes.DEAL_HAS_PROBLEM: "BIND_TO_DEAL_HAS_PROBLEM",
    EventTypes.DEAL_PROBLEM_RESOLVED: "BIND_TO_DEAL_PROBLEM_RESOLVED",
    EventTypes.DEAL_STATUS_CHANGED: "BIND_TO_DEAL_STATUS_CHANGED",
    EventTypes.NEW_REVIEW: "BIND_TO_NEW_REVIEW",
}

ACTION_BINDS = [
    "BIND_TO_PRE_DELIVERY", "BIND_TO_POST_DELIVERY",
    "BIND_TO_PRE_RAISE", "BIND_TO_POST_RAISE",
    "BIND_TO_POST_RESTORE",
]

ALL_BINDS = LIFECYCLE_BINDS + list(dict.fromkeys(EVENT_BINDS.values())) + ACTION_BINDS
PLUGIN_FIELDS = ["NAME", "VERSION", "DESCRIPTION", "CREDITS", "UUID", "SETTINGS_PAGE", "BIND_TO_DELETE"]


class PluginError(Exception):
    pass


class PluginData:
    def __init__(self, name: str, version: str, description: str, credits: str, uuid: str, path: str,
                 module: ModuleType, settings_page: bool, delete_handler: Callable | None, enabled: bool = True):
        self.name = name
        self.version = version
        self.description = description
        self.credits = credits
        self.uuid = uuid
        self.path = path
        self.module = module
        self.settings_page = settings_page
        self.delete_handler = delete_handler
        self.enabled = enabled
        self.commands: dict[str, str] = {}


def is_uuid_valid(value: str) -> bool:
    try:
        return str(UUID(value, version=4)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def is_plugin_file(path: str) -> bool:
    with open(path, "r", encoding="utf-8") as f:
        first = f.readline().strip()
    return not (first.startswith("#") and "noplug" in first.split())


def import_plugin(path: str) -> tuple[ModuleType, dict]:
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(f"plugins.{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"plugins.{name}"] = module
    spec.loader.exec_module(module)
    fields = {}
    for field in PLUGIN_FIELDS:
        if not hasattr(module, field):
            raise PluginError(f"в плагине {os.path.basename(path)} нет поля {field}")
        fields[field] = getattr(module, field)
    return module, fields


def is_enabled(uuid: str | None, settings: dict) -> bool:
    return uuid is None or settings.get("plugins", {}).get(uuid, True)


class PluginManager:
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = plugins_dir
        self.plugins: dict[str, PluginData] = {}
        self._handlers: dict[str, list[Callable]] = {name: [] for name in ALL_BINDS}

    def _register(self, module: ModuleType, uuid: str | None):
        for bind in ALL_BINDS:
            for func in getattr(module, bind, None) or []:
                func.plugin_uuid = uuid
                self._handlers[bind].append(func)

    def load(self) -> None:
        import handlers
        self._register(handlers, None)
        if not os.path.isdir(self.plugins_dir):
            return
        if self.plugins_dir not in sys.path:
            sys.path.append(self.plugins_dir)
        for file in sorted(f for f in os.listdir(self.plugins_dir) if f.endswith(".py")):
            path = os.path.join(self.plugins_dir, file)
            try:
                if not is_plugin_file(path):
                    continue
                module, data = import_plugin(path)
            except Exception:
                logging.getLogger("cardinal").exception(f"Не удалось загрузить плагин {file}")
                continue
            uuid = data["UUID"]
            if not is_uuid_valid(uuid):
                logging.getLogger("cardinal").error(f"Плагин {file}: неверный UUID {uuid}")
                continue
            if uuid in self.plugins:
                logging.getLogger("cardinal").error(
                    f"Плагин {file}: UUID {uuid} уже занят плагином «{self.plugins[uuid].name}»")
                continue
            self.plugins[uuid] = PluginData(str(data["NAME"]), str(data["VERSION"]), str(data["DESCRIPTION"]),
                                            str(data["CREDITS"]), uuid, path, module, bool(data["SETTINGS_PAGE"]),
                                            data["BIND_TO_DELETE"])
            try:
                self._register(module, uuid)
            except Exception:
                logging.getLogger("cardinal").exception(f"Ошибка регистрации хэндлеров плагина {file}")
                self.plugins.pop(uuid, None)
                continue
            logger.info(f"Плагин «{data['NAME']}» v{data['VERSION']} загружен")

    def handlers(self, bind: str) -> list[Callable]:
        return list(self._handlers.get(bind, []))

    def delete(self, uuid: str, context) -> None:
        plugin = self.plugins.pop(uuid)
        if callable(plugin.delete_handler):
            try:
                plugin.delete_handler(context)
            except Exception:
                logging.getLogger("cardinal").exception(f"Ошибка в BIND_TO_DELETE плагина «{plugin.name}»")
        for bind in self._handlers:
            self._handlers[bind] = [f for f in self._handlers[bind] if getattr(f, "plugin_uuid", None) != uuid]
        if os.path.exists(plugin.path):
            os.remove(plugin.path)
        logger.info(f"Плагин «{plugin.name}» удалён")

    def add_commands(self, uuid: str, commands: list[tuple[str, str, bool]]) -> None:
        if uuid not in self.plugins:
            return
        for command, description, _ in commands:
            self.plugins[uuid].commands[command] = description
