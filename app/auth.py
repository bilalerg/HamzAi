# auth.py — Profaix.com Entegrasyonlu Auth Sistemi
#
# AKIŞ:
# 1. Kullanıcı profaix.com'dan gelir → URL'de user_id var
# 2. profaix.com/api/users/{user_id} endpoint'inden doğrularız  ← DÜZELTİLDİ
# 3. Doğrulanırsa JWT cookie set ederiz
# 4. Sonraki isteklerde cookie'den okuruz

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
import httpx
import os
from app.core.logger import logger

# ── YAPILANDIRMA ──────────────────────────────────────────────────────
SECRET_KEY         = os.getenv("JWT_SECRET_KEY", "profaix-super-secret-key-2026")
ALGORITHM          = "HS256"
TOKEN_SURE         = timedelta(hours=8)
TOKEN_SURE_HATIRLA = timedelta(days=7)

# DÜZELTME 1: Tek kullanıcıyı doğrudan çeken endpoint
# Eskiden: GET /api/users?role=user  → tüm listeyi çekip Python'da arıyordu
# Şimdi:   GET /api/users/{user_id} → direkt o kullanıcıyı getirir
# Neden önemli: 100 kullanıcı varsa 100 kayıt çekiliyordu, şimdi 1 kayıt çekilir.
# Kullanıcı sayısı arttıkça fark çok daha kritik hale gelir.
PROFAIX_API = "https://profaix.com/api/users"


# ── PROFAİX.COM KULLANICI DOĞRULAMA ──────────────────────────────────
async def profaix_kullanici_dogrula(user_id: str) -> Optional[dict]:
    """
    profaix.com/api/users/{user_id} endpoint'inden kullanıcıyı doğrular.
    Bulursa kullanıcı bilgilerini döndürür, bulamazsa None.

    DÜZELTME: Eskiden tüm kullanıcı listesi çekilip Python'da döngüyle aranıyordu.
    Şimdi direkt o kullanıcının endpoint'i çağrılıyor → çok daha hızlı ve az veri.
    Eğer profaix.com'da /api/users/{id} endpoint'i yoksa aşağıdaki fallback devreye girer.
    """
    if not user_id:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:

            # ── ÖNCE: Direkt ID ile çek (ideal yol) ──────────────────
            res = await client.get(f"{PROFAIX_API}/{user_id}")

            if res.status_code == 200:
                kullanici = res.json()
                # Tek obje döndüyse direkt kullan
                if isinstance(kullanici, dict) and kullanici.get("id") == user_id:
                    logger.info(f"Kullanıcı doğrulandı (direkt): {user_id}")
                    return kullanici

            # ── FALLBACK: Endpoint yoksa eski yönteme dön ─────────────
            # profaix.com'da /api/users/{id} henüz yoksa bu devreye girer.
            # Çalışıyor ama yavaş — ileride kaldırılmalı.
            logger.warning(f"Direkt endpoint çalışmadı ({res.status_code}), liste taramasına geçiliyor...")
            res_liste = await client.get(PROFAIX_API, params={"role": "user"})

            if res_liste.status_code != 200:
                logger.error(f"Profaix API hatası: {res_liste.status_code}")
                return None

            kullanicilar = res_liste.json()
            for k in kullanicilar:
                if k.get("id") == user_id:
                    logger.info(f"Kullanıcı doğrulandı (liste): {user_id}")
                    return k

            logger.warning(f"Kullanıcı bulunamadı: {user_id}")
            return None

    except Exception as e:
        logger.error(f"Profaix kullanıcı doğrulama hatası: {e}")
        return None


# ── JWT TOKEN ────────────────────────────────────────────────────────
def token_uret(profaix_user_id: str, full_name: str, email: str,
               beni_hatirla: bool = False) -> str:
    sure   = TOKEN_SURE_HATIRLA if beni_hatirla else TOKEN_SURE
    expire = datetime.utcnow() + sure
    payload = {
        "sub":       profaix_user_id,
        "full_name": full_name,
        "email":     email,
        "exp":       expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def token_coz(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
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

    # Yeni format: profaix.com user_id (full_name var, username yok)
    if payload.get("full_name"):
        return {
            "id":               None,
            "username":         payload.get("full_name", ""),
            "profaix_user_id":  payload.get("sub", ""),
            "email":            payload.get("email", ""),
            "full_name":        payload.get("full_name", ""),
            "rol":              "user"
        }

    # Eski format: local admin (username/rol var)
    return {
        "id":               int(payload.get("sub", 0)),
        "username":         payload.get("username", ""),
        "profaix_user_id":  None,
        "email":            "",
        "full_name":        payload.get("username", ""),
        "rol":              payload.get("rol", "user")
    }


def giris_zorunlu(request: Request) -> dict:
    user = mevcut_kullanici(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Giriş yapmanız gerekiyor"
        )
    return user




# ── GİRİŞ LOGU ───────────────────────────────────────────────────────
def giris_kaydet(kullanici_adi: str, basarili: bool, giris_tipi: str,
                 ip: str = None, hata_sebebi: str = None):
    """Başarılı ve başarısız tüm giriş denemelerini kaydeder."""
    from app.core.database import SessionLocal, GirisLog
    db = SessionLocal()
    try:
        log = GirisLog(
            kullanici_adi = kullanici_adi,
            basarili      = 1 if basarili else 0,
            giris_tipi    = giris_tipi,
            hata_sebebi   = hata_sebebi,
            ip_adresi     = ip,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"Giriş log hatası: {e}")
    finally:
        db.close()

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
    admin_password = os.getenv("ADMIN_PASSWORD", "profaix2026")
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