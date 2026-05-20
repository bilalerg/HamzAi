# app/parasut/_http.py
# ---------------------------------------------------------------------
# PARAŞÜT HTTP KATMANI
#
# - Rate limit (tenant başına son istek zamanı, MIN_ISTEK_ARASI)
# - _istek_yap(): 4 deneme + exponential backoff (429/503/504 için)
# - parasut_get/post/delete/put/patch: ortak JSON helper'lar
# ---------------------------------------------------------------------

import time
import requests

from app.core.config import PARASUT_BASE_URL
from app.core.logger import logger

from ._state import _rate_limit_times, MIN_ISTEK_ARASI, _resolve_tid
from .token import get_token


# ── RATE LIMIT ───────────────────────────────────────────────────────

def _rate_limit_bekle(tid: str):
    son = _rate_limit_times.get(tid, 0)
    gecen = time.time() - son
    if gecen < MIN_ISTEK_ARASI:
        time.sleep(MIN_ISTEK_ARASI - gecen)
    _rate_limit_times[tid] = time.time()


# ── DÜŞÜK SEVİYE İSTEK + RETRY ───────────────────────────────────────

def _istek_yap(method: str, url: str, headers: dict, tid: str, **kwargs) -> requests.Response:
    """Rate limit beklemesi + exponential backoff retry (429/503/504)."""
    _rate_limit_bekle(tid)
    for deneme in range(4):
        response = requests.request(method, url, headers=headers, **kwargs)
        if response.status_code not in (429, 503, 504):
            return response
        retry_after = response.headers.get("Retry-After")
        bekleme = float(retry_after) if retry_after else (2 ** deneme)
        logger.warning(
            f"Paraşüt {response.status_code} (tenant={tid}) — {deneme + 1}. deneme, "
            f"{bekleme:.1f}s bekleniyor... (URL: {url})"
        )
        time.sleep(bekleme)
    return response


# ── PUBLIC JSON HELPER'LAR ───────────────────────────────────────────

def parasut_get(endpoint: str, params: dict = None, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    token = get_token(tid)
    url   = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "GET", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        tid=tid,
        params=params,
    )
    if response.status_code != 200:
        raise Exception(f"Paraşüt API hatası: {response.status_code} — {response.text[:200]}")
    return response.json()


def parasut_post(endpoint: str, veri: dict, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    token = get_token(tid)
    url   = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "POST", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"},
        tid=tid,
        json=veri,
    )
    if response.status_code not in [200, 201]:
        raise Exception(f"Paraşüt API hatası: {response.status_code} — {response.text[:200]}")
    return response.json()


def parasut_delete(endpoint: str, tenant_id=None) -> bool:
    tid   = _resolve_tid(tenant_id)
    token = get_token(tid)
    url   = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "DELETE", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"},
        tid=tid,
    )
    if response.status_code not in [200, 204]:
        raise Exception(f"Paraşüt DELETE hatası: {response.status_code} — {response.text[:200]}")
    return True


def parasut_put(endpoint: str, veri: dict, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    token = get_token(tid)
    url   = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "PUT", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"},
        tid=tid,
        json=veri,
    )
    if response.status_code not in [200, 201]:
        raise Exception(f"Paraşüt PUT hatası: {response.status_code} — {response.text[:200]}")
    return response.json()


def parasut_patch(endpoint: str, veri: dict = None, tenant_id=None) -> dict:
    """PATCH — Paraşüt'te archive / recover_from_archive / recover gibi action endpoint'leri için."""
    tid   = _resolve_tid(tenant_id)
    token = get_token(tid)
    url   = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "PATCH", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"},
        tid=tid,
        json=(veri or {}),
    )
    if response.status_code not in [200, 201, 202, 204]:
        raise Exception(f"Paraşüt PATCH hatası: {response.status_code} — {response.text[:200]}")
    if response.status_code == 204 or not response.text:
        return {}
    try:
        return response.json()
    except Exception:
        return {}
