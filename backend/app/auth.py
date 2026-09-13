from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Depends, Header, HTTPException

from .config import JWT_SECRET, TOKEN_EXPIRES_IN_SEC
from .db import get_user_by_id


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64).hex()
    return f"{salt}:{digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split(":", 1)
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64).hex()
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_jwt(payload: dict[str, Any]) -> str:
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps({**payload, "iat": now, "exp": now + TOKEN_EXPIRES_IN_SEC}, separators=(",", ":")).encode())
    raw = f"{header}.{body}".encode()
    sig = _b64(hmac.new(JWT_SECRET.encode(), raw, hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


def verify_jwt(token: str) -> dict[str, Any] | None:
    try:
        header, body, sig = token.split(".")
        expected = _b64(hmac.new(JWT_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_unb64(body))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    payload = verify_jwt(authorization[7:])
    if not payload:
        raise HTTPException(401, "登录已过期，请重新登录")
    uid = int(payload.get("uid", 0))
    user = get_user_by_id(uid)
    if not user:
        raise HTTPException(401, "用户不存在")
    return {"uid": uid, "username": user["username"]}
