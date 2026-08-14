"""设置 API：Webhook 配置 / 修改密码（M3）。"""
from fastapi import APIRouter, HTTPException

from ..auth import AuthUser, hash_password, verify_password
from ..models import SessionLocal, get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def get_settings_api(_user: str = AuthUser) -> dict:
    with SessionLocal() as session:
        s = get_settings(session)
        return {"webhook_url": s.webhook_url}


@router.put("")
def update_settings(body: dict, _user: str = AuthUser) -> dict:
    """可更新 webhook_url；改密码需提供 current_password + new_password。"""
    body = body or {}
    with SessionLocal() as session:
        s = get_settings(session)

        if "webhook_url" in body:
            url = str(body["webhook_url"]).strip()
            if url and not url.startswith(("http://", "https://")):
                raise HTTPException(422, "webhook_url 必须以 http:// 或 https:// 开头")
            s.webhook_url = url

        current = body.get("current_password")
        new_password = body.get("new_password")
        if current is not None or new_password is not None:
            if not s.password_hash:
                raise HTTPException(400, "尚未初始化密码")
            if not new_password:
                raise HTTPException(422, "新密码不能为空")
            if len(new_password) < 6:
                raise HTTPException(422, "新密码至少 6 位")
            if not current or not verify_password(str(current), s.password_hash):
                raise HTTPException(401, "当前密码错误")
            s.password_hash = hash_password(str(new_password))

        session.commit()
        return {"webhook_url": s.webhook_url}
