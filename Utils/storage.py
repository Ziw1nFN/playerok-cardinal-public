from __future__ import annotations

import json
import os
import re
import threading


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(root: str) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(root))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _clean(line: str) -> str:
    return line.replace("﻿", "").strip()


class AccountStorage:
    def __init__(self, root: str):
        self.root = root
        self.settings_path = os.path.join(root, "settings.json")
        self.products_dir = os.path.join(root, "products")
        self.cache_dir = os.path.join(root, "cache")
        self.plugins_dir = os.path.join(root, "plugins")
        self._lock = _lock_for(root)

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def products_path(self, name: str) -> str:
        return os.path.join(self.products_dir, name)

    def read_products(self, name: str) -> list[str]:
        path = self.products_path(name)
        if not os.path.exists(path):
            return []
        with self._lock, open(path, "r", encoding="utf-8") as f:
            return [_clean(line) for line in f if _clean(line)]

    def write_products(self, name: str, lines: list[str]) -> None:
        with self._lock:
            os.makedirs(self.products_dir, exist_ok=True)
            path = self.products_path(name)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))
            os.replace(tmp, path)

    def add_products(self, name: str, lines: list[str]) -> int:
        clean = [_clean(line) for line in lines if _clean(line)]
        with self._lock:
            self.write_products(name, self.read_products(name) + clean)
        return len(clean)

    def take_product(self, name: str) -> str | None:
        with self._lock:
            products = self.read_products(name)
            if not products:
                return None
            product = products.pop(0)
            self.write_products(name, products)
            return product

    def return_product(self, name: str, product: str) -> None:
        with self._lock:
            self.write_products(name, [product] + self.read_products(name))

    def count_products(self, name: str) -> int:
        return len(self.read_products(name))

    def product_files(self) -> list[str]:
        if not os.path.isdir(self.products_dir):
            return []
        return sorted(f for f in os.listdir(self.products_dir) if f.lower().endswith(".txt"))

    def _load_json(self, path: str, default):
        with self._lock:
            if not os.path.exists(path):
                return default
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                return default

    def _save_json(self, path: str, data) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

    def load_cache(self, name: str, default):
        return self._load_json(os.path.join(self.cache_dir, name), default)

    def save_cache(self, name: str, data) -> None:
        self._save_json(os.path.join(self.cache_dir, name), data)

    def _plugin_path(self, plugin: str) -> str:
        return os.path.join(self.plugins_dir, re.sub(r"[^\w\-]+", "_", plugin).strip("_") + ".json")

    def load_plugin_data(self, plugin: str, default):
        return self._load_json(self._plugin_path(plugin), default)

    def save_plugin_data(self, plugin: str, data) -> None:
        self._save_json(self._plugin_path(plugin), data)


GLOBAL = AccountStorage("storage")
