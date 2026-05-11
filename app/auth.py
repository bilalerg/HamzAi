# auth.py — Profaix.com Entegrasyonlu Auth Sistemi

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
import httpx
import os
from app.core.logger import logger
from app.core.config import JWT_SECRET_KEY

# ── YAPILANDIRMA ──────────────────────────────────────────────────────
# SECRET_KEY artık config.py'den geliyor — validate_config() boş geçmesine izin vermez.
ALGORITHM          = "HS256"
TOKEN_SURE         = timedelta(hours=8)
TOKEN_SURE_HATIRLA = timedelta(days=7)
PROFAIX_API        = "https://profaix.com/api/users"


# ── PROFAİX.COM KULLANICI DOĞRULAMA ──────────────────────────────────
async def profaix_kullanici_dogrula(user_id: str) -> Optional[dict]:
    """
    profaix.com/api/users?role=user endpoint'inden kullanıcıyı doğrular.
    Bulursa kullanıcı bilgilerini döndürür, bulamazsa None.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(PROFAIX_API, params={"role": "user"})
            if res.status_code != 200:
                logger.error(f"Profaix API hatası: {res.status_code}")
                return None
            kullanicilar = res.json()
            for k in kullanicilar:
                if k.get("id") == user_id:
                    return k
            return None
    except Exception as e:
        logger.error(f"Profaix kullanıcı doğrulama hatası: {e}")
        return None


# ── JWT TOKEN ────────────────────────────────────────────────────────
def token_uret(profaix_user_id: str, full_name: str, email: str,
               beni_hatirla: bool = False) -> str:
    sure    = TOKEN_SURE_HATIRLA if beni_hatirla else TOKEN_SURE
    expire  = datetime.utcnow() + sure
    payload = {
        "sub":       profaix_user_id,
        "full_name": full_name,
        "email":     email,
        "exp":       expire,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=ALGORITHM)


def token_coz(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


# ── MEVCUT KULLANICI ──────────────────────────────────────────────────
def mevcut_kullanici(request: Request) -> Optional[dict]:
    """Cookie'den token alır, kullanıcıyı döndürür.
    Hem eski (username/rol) hem yeni (profaix user_id) formatı destekler."""
    token = request.cookies.get("profaix_token")
    if not token:
        return None
    payload = token_coz(token)
    if not payload:
        return None

    # Yeni format: profaix.com user_id
    if payload.get("full_name"):
        return {
            "id": None,
            "username": payload.get("full_name", ""),
            "profaix_user_id": payload.get("sub", ""),
            "email": payload.get("email", ""),
            "full_name": payload.get("full_name", ""),
            "rol": "user"
        }

    # Eski format: local admin
    return {
        "id": int(payload.get("sub", 0)),
        "username": payload.get("username", ""),
        "profaix_user_id": None,
        "email": "",
        "full_name": payload.get("username", ""),
        "rol": payload.get("rol", "user")
    }


def giris_zorunlu(request: Request) -> dict:
    user = mevcut_kullanici(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Giriş yapmanız gerekiyor"
        )
    return user


# ── AUDİT LOG ────────────────────────────────────────────────────────
def audit_kaydet(user_id: int, username: str, intent: str,
                 params: dict = None, ip: str = None):
    import json
    from app.core.database import SessionLocal, AuditLog
    db = SessionLocal()
    try:
        log = AuditLog(
            user_id   = user_id,
            username  = username,
            intent    = intent,
            params    = json.dumps(params or {}, ensure_ascii=False),
            ip_adresi = ip
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"Audit log hatası: {e}")
    finally:
        db.close()


# ── İLK ADMIN ────────────────────────────────────────────────────────
def ilk_admin_olustur():
    """Geçici — profaix.com auth aktif olana kadar local admin."""
    from passlib.context import CryptContext
    from app.core.database import SessionLocal, User
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if not admin_password:
        logger.warning("⚠️ ADMIN_PASSWORD .env'de set edilmemiş, admin oluşturulmadı.")
        return
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db  = SessionLocal()
    try:
        mevcut = db.query(User).first()
        if mevcut:
            return
        admin = User(
            username      = admin_username.lower(),
            password_hash = pwd.hash(admin_password),
            ad_soyad      = "Admin",
            rol           = "admin",
            aktif         = 1
        )
        db.add(admin)
        db.commit()
        logger.info(f"✅ İlk admin oluşturuldu: {admin_username}")
    except Exception as e:
        logger.error(f"Admin oluşturma hatası: {e}")
    finally:
        db.close()