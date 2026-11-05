from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Header, HTTPException, Request, Response

from .config import JWT_SECRET, TOKEN_EXPIRES_IN_SEC
from .db import get_user_by_id

VIDEO_COOKIE = 'annotation-video-session'
VIDEO_COOKIE_PATH = '/api/track/video/'
EXPORT_COOKIE = 'annotation-export-session'
EXPORT_COOKIE_PATH = '/api/datasets/exports/'


def set_video_session(response: Response, request: Request, uid: int) -> None:
    response.set_cookie(VIDEO_COOKIE, sign_jwt({'uid': uid, 'purpose': 'source-video'}),
                        max_age=TOKEN_EXPIRES_IN_SEC, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path=VIDEO_COOKIE_PATH)
    # Same idea for a dataset ZIP: the browser saves it directly, so the link carries no token.
    response.set_cookie(EXPORT_COOKIE, sign_jwt({'uid': uid, 'purpose': 'dataset-download'}),
                        max_age=TOKEN_EXPIRES_IN_SEC, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path=EXPORT_COOKIE_PATH)


def clear_video_session(response: Response) -> None:
    response.delete_cookie(VIDEO_COOKIE, path=VIDEO_COOKIE_PATH, httponly=True, samesite='strict')
    response.delete_cookie(EXPORT_COOKIE, path=EXPORT_COOKIE_PATH, httponly=True, samesite='strict')


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
    if not payload or payload.get('purpose') is not None:
        raise HTTPException(401, "登录已过期，请重新登录")
    return _payload_user(payload)


def _payload_user(payload: dict[str, Any]) -> dict[str, Any]:
    uid = payload.get('uid')
    if type(uid) is not int or uid <= 0:
        raise HTTPException(401, '登录凭据无效')
    user = get_user_by_id(uid)
    if not user:
        raise HTTPException(401, "用户不存在")
    return {"uid": uid, "username": user["username"]}


def current_video_user(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    # An explicit invalid header cannot be hidden by an older browser cookie.
    if authorization is not None:
        return current_user(authorization)
    payload = verify_jwt(request.cookies.get(VIDEO_COOKIE, ''))
    if not payload or payload.get('purpose') != 'source-video':
        raise HTTPException(401, '未登录或视频播放凭据已过期，请重新登录')
    return _payload_user(payload)


def current_download_user(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if authorization is not None:
        return current_user(authorization)
    payload = verify_jwt(request.cookies.get(EXPORT_COOKIE, ''))
    if not payload or payload.get('purpose') != 'dataset-download':
        raise HTTPException(401, '未登录或下载凭据已过期，请重新登录')
    return _payload_user(payload)
