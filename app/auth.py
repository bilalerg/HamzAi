# auth.py — Profaix.com Entegrasyonlu Auth Sistemi

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
import httpx
import os
import uuid
from app.core.logger import logger
from app.core.config import JWT_SECRET_KEY, PROFAIX_EMBED_SECRET

# ── YAPILANDIRMA ──────────────────────────────────────────────────────
# SECRET_KEY artık config.py'den geliyor — validate_config() boş geçmesine izin vermez.
ALGORITHM          = "HS256"
TOKEN_SURE         = timedelta(hours=8)
TOKEN_SURE_HATIRLA = timedelta(days=7)
PROFAIX_API        = "https://profaix.com/api/users"


# ── PROFAİX.COM KULLANICI DOĞRULAMA ──────────────────────────────────
async def profaix_kullanici_dogrula(user_id: str) -> Optional[dict]:
    """
    profaix.com /users endpoint'inden kullanıcıyı doğrular (TÜM roller).
    Bulursa kullanıcı bilgilerini (role, is_active dahil) döndürür, bulamazsa None.
    Not: role filtresi YOK — operation/owner/superadmin hepsi bulunmalı.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(PROFAIX_API)
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
               beni_hatirla: bool = False, auth_session_id: str = None,
               rol: str = None) -> str:
    sure    = TOKEN_SURE_HATIRLA if beni_hatirla else TOKEN_SURE
    expire  = datetime.utcnow() + sure
    payload = {
        "sub":       profaix_user_id,
        "full_name": full_name,
        "email":     email,
        "typ":       "profaix",
        "exp":       expire,
    }
    if rol:
        payload["rol"] = rol
    if auth_session_id:
        payload["sid"] = auth_session_id
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=ALGORITHM)


def token_coz(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def embed_token_coz(token: str) -> Optional[dict]:
    """Profaix.com tarafından imzalanan kısa ömürlü iframe token'ını çözer."""
    if not PROFAIX_EMBED_SECRET:
        return None
    try:
        return jwt.decode(token, PROFAIX_EMBED_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        return None


def _request_token_al(request: Request) -> Optional[str]:
    """Önce cookie, yoksa Authorization: Bearer token okur."""
    token = request.cookies.get("profaix_token")
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def _principal_key(payload: dict) -> Optional[str]:
    sub = payload.get("sub")
    if not sub:
        return None
    if payload.get("typ") == "profaix" or payload.get("full_name"):
        return "pfa:" + str(sub)
    return "loc:" + str(sub)


def aktif_oturum_baslat(principal_key: str, username: str = "", request: Request = None) -> str:
    """Kullanıcı için tek aktif oturumu günceller; eski cihazlar bu anda düşer."""
    from app.core.database import SessionLocal, ActiveLogin
    sid = uuid.uuid4().hex
    db = SessionLocal()
    try:
        row = db.query(ActiveLogin).filter(ActiveLogin.principal_key == principal_key).first()
        if not row:
            row = ActiveLogin(principal_key=principal_key, session_id=sid)
            db.add(row)
        else:
            row.session_id = sid
        row.username = username or row.username
        row.user_agent = request.headers.get("user-agent", "")[:1000] if request else None
        row.ip_adresi = request.client.host if request and request.client else None
        row.updated_at = datetime.utcnow()
        db.commit()
        return sid
    except Exception as e:
        db.rollback()
        logger.error(f"Aktif oturum başlatılamadı: {e}")
        raise
    finally:
        db.close()


def aktif_oturum_gecerli(principal_key: str, session_id: str) -> bool:
    if not principal_key or not session_id:
        return False
    from app.core.database import SessionLocal, ActiveLogin
    db = SessionLocal()
    try:
        row = db.query(ActiveLogin).filter(ActiveLogin.principal_key == principal_key).first()
        if not row or row.session_id != session_id:
            return False
        row.updated_at = datetime.utcnow()
        db.commit()
        return True
    except Exception as e:
        logger.error(f"Aktif oturum kontrol hatası: {e}")
        return False
    finally:
        db.close()


def aktif_oturum_kapat(principal_key: str, session_id: str = None):
    from app.core.database import SessionLocal, ActiveLogin
    db = SessionLocal()
    try:
        row = db.query(ActiveLogin).filter(ActiveLogin.principal_key == principal_key).first()
        if row and (session_id is None or row.session_id == session_id):
            db.delete(row)
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Aktif oturum kapatılamadı: {e}")
    finally:
        db.close()


def _payload_kullanici(payload: dict) -> Optional[dict]:
    if payload.get("typ") == "profaix" or payload.get("full_name"):
        return {
            "id": None,
            "username": payload.get("full_name", ""),
            "profaix_user_id": payload.get("sub", ""),
            "email": payload.get("email", ""),
            "full_name": payload.get("full_name", ""),
            "rol": payload.get("rol") or "owner",  # eski token (rol yok) → owner grace
            "auth_session_id": payload.get("sid", ""),
        }

    try:
        local_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        return None

    return {
        "id": local_id,
        "username": payload.get("username", ""),
        "profaix_user_id": None,
        "email": "",
        "full_name": payload.get("username", ""),
        "rol": payload.get("rol", "user"),
        "auth_session_id": payload.get("sid", ""),
    }


def token_durumu(request: Request) -> tuple[Optional[dict], str]:
    token = _request_token_al(request)
    if not token:
        return None, "missing"
    payload = token_coz(token)
    if not payload:
        return None, "invalid"
    principal_key = _principal_key(payload)
    session_id = payload.get("sid")
    if not aktif_oturum_gecerli(principal_key, session_id):
        return None, "replaced"
    user = _payload_kullanici(payload)
    if not user:
        return None, "invalid"
    return user, ""


# ── MEVCUT KULLANICI ──────────────────────────────────────────────────
def mevcut_kullanici(request: Request) -> Optional[dict]:
    """Cookie veya Authorization header'dan token alır, kullanıcıyı döndürür.
    Hem eski (username/rol) hem yeni (profaix user_id) formatı destekler."""
    return token_durumu(request)[0]


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