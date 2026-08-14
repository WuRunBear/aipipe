"""认证 API：状态 / 首次设置密码 / 登录（M3）。"""
from fastapi import APIRouter, HTTPException

from ..auth import create_token, hash_password, verify_password
from ..models import SessionLocal, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LEN = 6


@router.get("/status")
def auth_status() -> dict:
    with SessionLocal() as session:
        s = get_settings(session)
        return {"initialized": bool(s.password_hash)}


@router.post("/setup")
def auth_setup(body: dict) -> dict:
    """首次初始化：设置单用户密码（仅未初始化时可用）。"""
    password = (body or {}).get("password", "")
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(422, f"密码至少 {MIN_PASSWORD_LEN} 位")
    with SessionLocal() as session:
        s = get_settings(session)
        if s.password_hash:
            raise HTTPException(400, "已初始化，请直接登录")
        s.password_hash = hash_password(password)
        session.commit()
    return {"token": create_token(), "expires_in": 86400}


@router.post("/login")
def auth_login(body: dict) -> dict:
    password = (body or {}).get("password", "")
    with SessionLocal() as session:
        s = get_settings(session)
        if not s.password_hash:
            raise HTTPException(400, "尚未初始化密码，请先设置")
        if not verify_password(password, s.password_hash):
            raise HTTPException(401, "密码错误")
    return {"token": create_token(), "expires_in": 86400}
