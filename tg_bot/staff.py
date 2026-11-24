from __future__ import annotations

import secrets
import threading
import time

from Utils import tools

INVITE_TTL = 24 * 3600


class StaffStore:
    def __init__(self, path: str = "tg_staff.json"):
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> dict:
        data = tools.load_cache(self.path, {})
        data.setdefault("staff", {})
        data.setdefault("invites", {})
        return data

    def _save(self, data: dict) -> None:
        tools.save_cache(self.path, data)

    def get(self, user_id: int) -> dict | None:
        with self._lock:
            member = self._load()["staff"].get(str(user_id))
            return dict(member) if member else None

    def all(self) -> dict[int, dict]:
        with self._lock:
            return {int(uid): dict(m) for uid, m in self._load()["staff"].items()}

    def add(self, user_id: int, name: str, perms: list[str], accounts: list[str]) -> None:
        with self._lock:
            data = self._load()
            data["staff"][str(user_id)] = {"name": name, "perms": list(perms), "accounts": list(accounts),
                                           "added": time.time()}
            self._save(data)

    def remove(self, user_id: int) -> bool:
        with self._lock:
            data = self._load()
            removed = data["staff"].pop(str(user_id), None) is not None
            if removed:
                self._save(data)
            return removed

    def _toggle(self, user_id: int, field: str, value: str, on: bool) -> None:
        with self._lock:
            data = self._load()
            member = data["staff"].get(str(user_id))
            if member is None:
                return
            values = [v for v in member.get(field, []) if v != value]
            if on:
                values.append(value)
            member[field] = values
            self._save(data)

    def set_perm(self, user_id: int, perm: str, on: bool) -> None:
        self._toggle(user_id, "perms", perm, on)

    def set_account(self, user_id: int, acc_id: str, on: bool) -> None:
        self._toggle(user_id, "accounts", acc_id, on)

    def create_invite(self, perms: list[str], accounts: list[str], created_by: int, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        code = secrets.token_urlsafe(16)
        with self._lock:
            data = self._load()
            data["invites"] = {k: v for k, v in data["invites"].items() if v.get("expires", 0) > time.time()}
            data["invites"][code] = {"perms": list(perms), "accounts": list(accounts), "created_by": created_by,
                                     "expires": now + INVITE_TTL}
            self._save(data)
        return code

    def revoke_invites(self, created_by: int | None = None) -> int:
        with self._lock:
            data = self._load()
            keep = {k: v for k, v in data["invites"].items()
                    if created_by is not None and v.get("created_by") != created_by}
            revoked = len(data["invites"]) - len(keep)
            data["invites"] = keep
            self._save(data)
            return revoked

    def take_invite(self, code: str, now: float | None = None) -> dict | None:
        now = now if now is not None else time.time()
        with self._lock:
            data = self._load()
            invite = data["invites"].pop(code, None)
            if invite is not None:
                self._save(data)
        if invite is None or invite.get("expires", 0) <= now:
            return None
        return invite
