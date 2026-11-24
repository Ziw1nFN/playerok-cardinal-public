from __future__ import annotations

from playerokapi.exceptions import BotCheckDetectedException, RequestFailedError, UnauthorizedError

FATAL_TEXTS = {
    UnauthorizedError: "Playerok не принял токен. Обновите токен аккаунта",
    BotCheckDetectedException: "Сработала защита Playerok от ботов. Обновите cookies или задайте прокси",
}

FATAL_ERRORS = tuple(FATAL_TEXTS)


def fatal_text(error: BaseException) -> str | None:
    return next((text for kind, text in FATAL_TEXTS.items() if isinstance(error, kind)), None)


def short_error(error: BaseException, limit: int = 200) -> str:
    if isinstance(error, RequestFailedError):
        return f"Playerok ответил кодом {error.status_code}"
    text = " ".join(str(error).split()) or type(error).__name__
    return text if len(text) <= limit else text[:limit - 1] + "…"
