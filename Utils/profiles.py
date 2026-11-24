from __future__ import annotations

import copy

from Utils.settings import DEAL_REPLY_KINDS

RAISE_MODES = ("default", "off", "custom")


def new_profile(profile_id: str, title: str) -> dict:
    return {
        "id": profile_id,
        "title": title,
        "enabled": True,
        "filters": {"words": [], "price_min": 0, "price_max": 0, "places": []},
        "raise": {"mode": "default", "interval": 60, "max_price": 30},
        "deal_replies": {kind: "" for kind in DEAL_REPLY_KINDS},
        "auto_responses": [],
    }


def _as_int(value, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        value = value.split("|")
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]


def _as_responses(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for entry in value:
        if isinstance(entry, dict) and _as_list(entry.get("commands")) and isinstance(entry.get("text"), str):
            result.append({**entry, "commands": _as_list(entry.get("commands")), "text": entry["text"]})
    return result


def normalize(profile: dict) -> dict:
    result = new_profile(str(profile.get("id", "")), str(profile.get("title", "")))
    filters = profile.get("filters") if isinstance(profile.get("filters"), dict) else {}
    raise_cfg = profile.get("raise") if isinstance(profile.get("raise"), dict) else {}
    replies = profile.get("deal_replies") if isinstance(profile.get("deal_replies"), dict) else {}
    result["enabled"] = bool(profile.get("enabled", True))
    result["filters"] = {"words": _as_list(filters.get("words")), "price_min": _as_int(filters.get("price_min")),
                         "price_max": _as_int(filters.get("price_max")), "places": _as_list(filters.get("places"))}
    mode = raise_cfg.get("mode")
    result["raise"] = {"mode": mode if mode in RAISE_MODES else "default",
                       "interval": _as_int(raise_cfg.get("interval"), 60) or 60,
                       "max_price": _as_int(raise_cfg.get("max_price"), 30)}
    result["deal_replies"] = {kind: replies[kind] if isinstance(replies.get(kind), str) else ""
                              for kind in DEAL_REPLY_KINDS}
    result["auto_responses"] = _as_responses(profile.get("auto_responses"))
    return copy.deepcopy(result)


def has_conditions(profile: dict) -> bool:
    f = normalize(profile)["filters"]
    return bool(f["words"] or f["places"] or f["price_min"] > 0 or f["price_max"] > 0)


def uses_places(profiles: list) -> bool:
    return any(normalize(p)["enabled"] and normalize(p)["filters"]["places"] for p in profiles)


def _contains_any(text: str | None, words: list) -> bool:
    lowered = (text or "").lower()
    return any(w.strip().lower() in lowered for w in words if w and w.strip())


def needs_places(profiles: list, name: str | None, price: int | None) -> bool:
    return any(normalize(p)["filters"]["places"] and matches(p, name, price, None, check_places=False)
               for p in profiles)


def matches(profile: dict, name: str | None, price: int | None, places: list | None,
            check_places: bool = True) -> bool:
    p = normalize(profile)
    if not p["enabled"] or not has_conditions(p):
        return False
    f = p["filters"]
    if f["words"] and not _contains_any(name, f["words"]):
        return False
    if f["price_min"] > 0 or f["price_max"] > 0:
        if price is None:
            return False
        if f["price_min"] > 0 and price < f["price_min"]:
            return False
        if f["price_max"] > 0 and price > f["price_max"]:
            return False
    if check_places and f["places"] and not _contains_any(" | ".join(places or []), f["places"]):
        return False
    return True


def match(profiles: list, name: str | None, price: int | None, places: list | None) -> dict | None:
    for profile in profiles:
        if matches(profile, name, price, places):
            return normalize(profile)
    return None
