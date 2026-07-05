"""Telegram Mini App orqali yuboriladigan initData ni tekshirish (rasmiy Telegram algoritmi)."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    if not init_data:
        raise InitDataError("initData yo'q")

    parsed = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise InitDataError("hash topilmadi")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InitDataError("Imzo noto'g'ri")

    auth_date = int(parsed.get("auth_date", 0))
    if max_age_seconds and (time.time() - auth_date) > max_age_seconds:
        raise InitDataError("initData eskirgan")

    user_raw = parsed.get("user")
    if not user_raw:
        raise InitDataError("Foydalanuvchi ma'lumoti yo'q")

    return json.loads(user_raw)
