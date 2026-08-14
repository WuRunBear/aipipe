"""认证：bcrypt 密码 + JWT（M3，单用户）。

- secret 持久化在 `data/jwt_secret`（自动生成），环境变量 `AIPIPE_JWT_SECRET` 可覆盖。
- 除 SSE 等无法携带 header 的场景外，token 一律走 `Authorization: Bearer`。
- token 有效期 24h。
"""
import datetime
import os
import secrets
from pathlib import Path

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from .config import DATA_DIR

ALGORITHM = "HS256"
TOKEN_TTL = datetime.timedelta(hours=24)

_secret: str | None = None


def _load_secret() -> str:
    global _secret
    if _secret is not None:
        return _secret
    env = os.environ.get("AIPIPE_JWT_SECRET")
    if env:
        _secret = env
        return _secret
    path = Path(DATA_DIR) / "jwt_secret"
    if path.is_file():
        _secret = path.read_text(encoding="utf-8").strip()
    else:
        _secret = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_secret, encoding="utf-8")
        path.chmod(0o600)
    return _secret


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def create_token() -> str:
    payload = {
        "sub": "admin",
        "iat": datetime.datetime.now(datetime.timezone.utc),
        "exp": datetime.datetime.now(datetime.timezone.utc) + TOKEN_TTL,
    }
    return jwt.encode(payload, _load_secret(), algorithm=ALGORITHM)


def _decode_token(token: str) -> None:
    try:
        jwt.decode(token, _load_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as e:
        raise HTTPException(401, "登录已失效，请重新登录") from e


def require_auth(request: Request) -> str:
    """标准鉴权依赖：仅接受 Authorization header。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = auth[7:]
    _decode_token(token)
    return "admin"


def require_auth_any(request: Request) -> str:
    """放宽鉴权：header 优先，允许 query `token`（仅用于 SSE 等场景）。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        _decode_token(auth[7:])
        return "admin"
    token = request.query_params.get("token")
    if token:
        _decode_token(token)
        return "admin"
    raise HTTPException(401, "未登录")


AuthUser = Depends(require_auth)
AuthUserAny = Depends(require_auth_any)
