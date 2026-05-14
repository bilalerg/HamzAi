# tenant.py — Tenant context yönetimi
# Her request için aktif tenant'ı belirler ve Paraşüt credentials'larını döner

import time
from typing import Optional

from app.core.logger import logger

_TENANT_CACHE_TTL = 300  # 5 dakika

_tenant_cache: dict = {}
_tenant_cache_time: dict = {}


def _default_tenant_id() -> str:
    from app.core.config import PARASUT_USERNAME
    return PARASUT_USERNAME or "__default__"


# Modül seviyesinde lazy init — import sırasında config yüklenmesini bekler
DEFAULT_TENANT_ID: str = ""


def get_default_tenant_id() -> str:
    global DEFAULT_TENANT_ID
    if not DEFAULT_TENANT_ID:
        DEFAULT_TENANT_ID = _default_tenant_id()
    return DEFAULT_TENANT_ID


def get_tenant(tenant_id: str) -> Optional[object]:
    """DB'den tenant çeker, 5 dakika TTL ile cache'ler. Kendi session'ını açar."""
    from app.core.database import Tenant, SessionLocal
    now = time.time()
    if tenant_id in _tenant_cache and now - _tenant_cache_time.get(tenant_id, 0) < _TENANT_CACHE_TTL:
        return _tenant_cache[tenant_id]
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id, Tenant.aktif == 1).first()
    except Exception as e:
        logger.error(f"Tenant DB sorgusu hatası ({tenant_id}): {e}")
        return _tenant_cache.get(tenant_id)
    finally:
        db.close()
    _tenant_cache[tenant_id] = tenant
    _tenant_cache_time[tenant_id] = now
    return tenant


def get_tenant_credentials(tenant_id: str) -> dict:
    """Paraşüt credentials dict döner. tenant_id=None → default tenant."""
    from app.core.config import (
        PARASUT_CLIENT_ID, PARASUT_CLIENT_SECRET,
        PARASUT_USERNAME, PARASUT_PASSWORD,
        PARASUT_COMPANY_ID, PARASUT_HURDA_PRODUCT_ID,
    )
    _default = {
        "client_id":        PARASUT_CLIENT_ID,
        "client_secret":    PARASUT_CLIENT_SECRET,
        "username":         PARASUT_USERNAME,
        "password":         PARASUT_PASSWORD,
        "company_id":       PARASUT_COMPANY_ID,
        "hurda_product_id": PARASUT_HURDA_PRODUCT_ID or "",
    }
    default = get_default_tenant_id()
    if not tenant_id or tenant_id == default:
        return _default
    tenant = get_tenant(tenant_id)
    if not tenant:
        logger.debug(f"Tenant kaydı yok: {tenant_id}, varsayılan credentials kullanılıyor")
        return _default
    return {
        "client_id":        tenant.parasut_client_id,
        "client_secret":    tenant.parasut_client_secret,
        "username":         tenant.parasut_username,
        "password":         tenant.parasut_password,
        "company_id":       tenant.parasut_company_id,
        "hurda_product_id": tenant.parasut_hurda_product_id or "",
    }


def invalidate_tenant_cache(tenant_id: str):
    """Tenant cache'ini geçersiz kılar (yeni tenant eklendiğinde çağrılır)."""
    _tenant_cache.pop(tenant_id, None)
    _tenant_cache_time.pop(tenant_id, None)
