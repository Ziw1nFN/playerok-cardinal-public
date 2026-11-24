from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime

ENV_FIELDS = {
    "token": "TOKEN",
    "cookies": "COOKIES",
    "ddg5": "DDG5",
    "proxy": "PROXY",
    "user_agent": "USER_AGENT",
}

_PROXY = re.compile(r"^(?:[^:@\s]+:[^@\s]+@)?[A-Za-z0-9.\-]+:(\d{1,5})$")


class AccountError(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


@dataclass
class AccountInfo:
    id: str
    username: str = ""
    enabled: bool = True


def env_keys(acc_id: str) -> dict[str, str]:
    return {field: f"ACC_{acc_id}_{suffix}" for field, suffix in ENV_FIELDS.items()}


def normalize_proxy(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
    match = _PROXY.match(value)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        raise AccountError("Прокси в формате ip:port или user:pass@ip:port")
    return value


class AccountRegistry:
    def __init__(self, path: str = "accounts.json", max_accounts: int = 5):
        self.path = path
        self.max_accounts = max_accounts
        self.root = os.path.join(os.path.dirname(path) or ".", "accounts")
        self._lock = threading.RLock()
        self._next = 1
        self.accounts: list[AccountInfo] = []
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._next = int(data.get("next", 1))
        self.accounts = [AccountInfo(**item) for item in data.get("accounts", [])]

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"next": self._next, "accounts": [asdict(a) for a in self.accounts]}, f,
                          ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def folder(self, acc_id: str) -> str:
        return os.path.join(self.root, acc_id)

    def get(self, acc_id: str) -> AccountInfo | None:
        return next((a for a in self.accounts if a.id == acc_id), None)

    def by_username(self, name: str) -> AccountInfo | None:
        name = (name or "").lower()
        return next((a for a in self.accounts if name and a.username.lower() == name), None)

    def add(self, username: str) -> AccountInfo:
        with self._lock:
            if len(self.accounts) >= self.max_accounts:
                raise AccountError(f"Достигнут лимит аккаунтов: {self.max_accounts}")
            info = AccountInfo(id=f"a{self._next}", username=username)
            self._next += 1
            self.accounts.append(info)
            self.save()
            return info

    def remove(self, acc_id: str) -> str:
        with self._lock:
            info = self.get(acc_id)
            if info is None:
                raise AccountError("Аккаунт не найден")
            target = os.path.join(self.root, "_removed", f"{acc_id}-{datetime.now():%Y%m%d-%H%M%S}")
            if os.path.isdir(self.folder(acc_id)):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.replace(self.folder(acc_id), target)
            self.accounts.remove(info)
            self.save()
            return target

    def set_enabled(self, acc_id: str, enabled: bool) -> None:
        with self._lock:
            self.get(acc_id).enabled = enabled
            self.save()

    def set_username(self, acc_id: str, name: str) -> None:
        with self._lock:
            self.get(acc_id).username = name
            self.save()
