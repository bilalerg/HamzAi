# app/parasut/token.py
# ---------------------------------------------------------------------
# PARAŞÜT TOKEN YÖNETİMİ — per-tenant izole
#
# Tüm fonksiyonlar tenant_id parametresi alır.
# tenant_id=None → DEFAULT_TENANT_ID (config.py'deki PARASUT_USERNAME).
# Her tenant için ayrı access/refresh token + lock tutulur (_state.py).
# ---------------------------------------------------------------------

import time
import requests

from app.core.config import (
    PARASUT_CLIENT_ID,
    PARASUT_CLIENT_SECRET,
    PARASUT_USERNAME,
    PARASUT_PASSWORD,
    PARASUT_COMPANY_ID,
    PARASUT_HURDA_PRODUCT_ID,
)
from app.core.logger import logger

from ._state import (
    _tokens, _token_locks,
    _resolve_tid, _get_lock,
)


def _tenant_credentials(tid: str) -> dict:
    """
    Tenant credentials döner.
    DEFAULT tenant → global config.
    Diğerleri → DB'den Tenant kaydı (app.core.tenant.get_tenant_credentials).
    """
    default = _resolve_tid(None)
    if tid == default:
        return {
            "client_id":        PARASUT_CLIENT_ID,
            "client_secret":    PARASUT_CLIENT_SECRET,
            "username":         PARASUT_USERNAME,
            "password":         PARASUT_PASSWORD,
            "company_id":       PARASUT_COMPANY_ID,
            "hurda_product_id": PARASUT_HURDA_PRODUCT_ID or "",
        }
    try:
        from app.core.tenant import get_tenant_credentials
        return get_tenant_credentials(tid)
    except Exception as e:
        logger.error(f"Tenant credentials alınamadı ({tid}): {e}")
        return {
            "client_id":        PARASUT_CLIENT_ID,
            "client_secret":    PARASUT_CLIENT_SECRET,
            "username":         PARASUT_USERNAME,
            "password":         PARASUT_PASSWORD,
            "company_id":       PARASUT_COMPANY_ID,
            "hurda_product_id": PARASUT_HURDA_PRODUCT_ID or "",
        }


def get_tenant_config(tenant_id=None) -> dict:
    """company_id ve hurda_product_id döner — main.py endpoint'lerinde kullanılır."""
    tid = _resolve_tid(tenant_id)
    try:
        creds = _tenant_credentials(tid)
        return {
            "company_id":       creds["company_id"],
            "hurda_product_id": creds["hurda_product_id"] or PARASUT_HURDA_PRODUCT_ID or "",
        }
    except Exception:
        return {
            "company_id":       PARASUT_COMPANY_ID,
            "hurda_product_id": PARASUT_HURDA_PRODUCT_ID or "",
        }


def _token_al(tid: str) -> dict:
    creds = _tenant_credentials(tid)
    logger.info(f"Paraşüt'ten yeni token alınıyor (tenant={tid})...")
    response = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type":    "password",
            "client_id":     creds["client_id"],
            "client_secret": creds["client_secret"],
            "username":      creds["username"],
            "password":      creds["password"],
            "redirect_uri":  "urn:ietf:wg:oauth:2.0:oob",
        }
    )
    if response.status_code != 200:
        raise Exception(f"Paraşüt token hatası: {response.status_code}")
    logger.info(f"Paraşüt token alındı (tenant={tid})")
    return response.json()


def _token_yenile(tid: str, refresh_token: str) -> dict:
    creds = _tenant_credentials(tid)
    logger.info(f"Paraşüt token yenileniyor (tenant={tid})...")
    response = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type":    "refresh_token",
            "client_id":     creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": refresh_token,
            "redirect_uri":  "urn:ietf:wg:oauth:2.0:oob",
        }
    )
    if response.status_code != 200:
        return _token_al(tid)
    logger.info(f"Paraşüt token yenilendi (tenant={tid})")
    return response.json()


def get_token(tenant_id=None) -> str:
    """
    Tenant'ın access token'ını döner. Süresi dolmuşsa otomatik yeniler.
    Eğer hiç token yoksa _token_al() ile yeni alır.
    Double-checked locking ile concurrent çağrılar tek token alır.
    """
    tid = _resolve_tid(tenant_id)

    # Kilitsiz hızlı okuma
    token_data = _tokens.get(tid)
    if token_data and time.time() <= token_data["expires_at"] - 60:
        return token_data["access"]

    lock = _get_lock(_token_locks, tid)
    with lock:
        # Double-checked locking
        token_data = _tokens.get(tid)
        if token_data and time.time() <= token_data["expires_at"] - 60:
            return token_data["access"]

        if token_data is None:
            veri = _token_al(tid)
        else:
            veri = _token_yenile(tid, token_data["refresh"])

        _tokens[tid] = {
            "access":     veri["access_token"],
            "refresh":    veri["refresh_token"],
            "expires_at": time.time() + veri["expires_in"],
        }
        return _tokens[tid]["access"]
