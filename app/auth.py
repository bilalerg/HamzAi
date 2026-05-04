# auth.py — Kimlik Doğrulama Sistemi
#
# NELER VAR?
# 1. Şifre hashleme — bcrypt ile şifreyi güvenli sakla
# 2. JWT token üretme ve doğrulama
# 3. "Beni hatırla" — token süresini 7 güne uzat
# 4. FastAPI dependency — her endpoint "kim bu?" diye sorabiliyor
# 5. İlk admin kullanıcısını otomatik oluşturma

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Request, HTTPException, status
from app.core.database import SessionLocal, User, AuditLog
from app.core.logger import logger
import json
import os

# ── GİZLİ ANAHTAR ────────────────────────────────────────────────────
# JWT token'ları bu anahtarla imzalanır.
# .env'de JWT_SECRET_KEY tanımlanmamışsa varsayılan kullanılır ama
# production'da mutlaka .env'e ekle.
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "profaix-super-secret-key-2026-degistir")
ALGORITHM  = "HS256"

# Token süreleri
TOKEN_SURE_NORMAL     = timedelta(hours=8)    # Normal giriş — 8 saat
TOKEN_SURE_HATIRLA    = timedelta(days=7)     # Beni hatırla — 7 gün

# ── ŞİFRE HASHLEME ───────────────────────────────────────────────────
# bcrypt: şifreyi tek yönlü karıştırır.
# "abc123" → "$2b$12$xyz..." gibi bir hash çıkar.
# Orijinal şifreye geri dönemezsin — bu güvenlik için istenen şey.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def sifre_hashle(sifre: str) -> str:
    """Düz şifreyi → bcrypt hash'e çevirir."""
    return pwd_context.hash(sifre)

def sifre_dogrula(sifre: str, hash: str) -> bool:
    """Girilen şifre ile kayıttaki hash eşleşiyor mu?"""
    return pwd_context.verify(sifre, hash)


# ── JWT TOKEN ─────────────────────────────────────────────────────────
def token_uret(user_id: int, username: str, rol: str, beni_hatirla: bool = False) -> str:
    """
    JWT token üretir.
    Token içine user_id, username, rol koyar.
    beni_hatirla=True ise 7 gün, False ise 8 saat geçerli.
    """
    sure = TOKEN_SURE_HATIRLA if beni_hatirla else TOKEN_SURE_NORMAL
    expire = datetime.utcnow() + sure
    payload = {
        "sub": str(user_id),      # subject — kimin tokeni
        "username": username,
        "rol": rol,
        "exp": expire,
        "beni_hatirla": beni_hatirla
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def token_coz(token: str) -> Optional[dict]:
    """
    Token'ı çözer, içindeki bilgileri döndürür.
    Geçersiz veya süresi dolmuşsa None döner.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


# ── KULLANICI DOĞRULAMA ───────────────────────────────────────────────
def kullanici_giris(username: str, sifre: str) -> Optional[dict]:
    """
    Kullanıcı adı ve şifreyi kontrol eder.
    Doğruysa dict döner — session kapandıktan sonra da kullanılabilir.
    """
    db = SessionLocal()
    try:
        user = db.query(User).filter(
            User.username == username.strip().lower(),
            User.aktif == 1
        ).first()
        if not user:
            return None
        if not sifre_dogrula(sifre, user.password_hash):
            return None
        user_id = user.id
        user_username = user.username
        user_rol = user.rol
        user_ad = user.ad_soyad
        user.son_giris = datetime.utcnow()
        db.commit()
        return {"id": user_id, "username": user_username, "rol": user_rol, "ad_soyad": user_ad}
    except Exception as e:
        logger.error(f"Giriş hatası: {e}")
        return None
    finally:
        db.close()


# ── FASTAPI DEPENDENCY ────────────────────────────────────────────────
# Bu fonksiyon endpoint'lerde "kim bu?" sorusunu cevaplar.
# Cookie'den token'ı alır, çözer, kullanıcıyı döndürür.

def mevcut_kullanici(request: Request) -> Optional[dict]:
    """
    Cookie'den token alır, kullanıcıyı döndürür.
    Token yoksa veya geçersizse None döner.
    """
    token = request.cookies.get("profaix_token")
    if not token:
        return None
    payload = token_coz(token)
    if not payload:
        return None
    return {
        "id": int(payload.get("sub", 0)),
        "username": payload.get("username", ""),
        "rol": payload.get("rol", "staff")
    }


def giris_zorunlu(request: Request) -> dict:
    """
    Giriş yapılmamışsa 401 hatası fırlatır.
    Endpoint'lerde: def endpoint(user = Depends(giris_zorunlu))
    """
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
    """
    Kullanıcı bir intent tetiklediğinde çağrılır.
    Kim, ne zaman, ne yaptı → audit_logs tablosuna yazar.
    """
    db = SessionLocal()
    try:
        log = AuditLog(
            user_id=user_id,
            username=username,
            intent=intent,
            params=json.dumps(params or {}, ensure_ascii=False),
            ip_adresi=ip
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"Audit log hatası: {e}")
    finally:
        db.close()


# ── İLK ADMIN KULLANICISI ─────────────────────────────────────────────
# Uygulama ilk başladığında hiç kullanıcı yoksa otomatik admin oluşturur.
# Kullanıcı adı ve şifre .env'den gelir.

def ilk_admin_olustur():
    """
    Veritabanında hiç kullanıcı yoksa .env'deki
    ADMIN_USERNAME ve ADMIN_PASSWORD ile admin oluşturur.
    """
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "profaix2026")

    db = SessionLocal()
    try:
        mevcut = db.query(User).first()
        if mevcut:
            return  # Zaten kullanıcı var

        admin = User(
            username=admin_username.lower(),
            password_hash=sifre_hashle(admin_password),
            ad_soyad="Admin",
            rol="admin",
            aktif=1
        )
        db.add(admin)
        db.commit()
        logger.info(f"✅ İlk admin oluşturuldu: {admin_username}")
        logger.warning("⚠️  Lütfen .env'e ADMIN_USERNAME ve ADMIN_PASSWORD ekleyin!")
    except Exception as e:
        logger.error(f"Admin oluşturma hatası: {e}")
    finally:
        db.close()