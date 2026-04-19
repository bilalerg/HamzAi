# client.py — Paraşüt API İstemcisi

import requests
import time
from app.core.config import (
    PARASUT_CLIENT_ID,
    PARASUT_CLIENT_SECRET,
    PARASUT_USERNAME,
    PARASUT_PASSWORD,
    PARASUT_COMPANY_ID,
    PARASUT_BASE_URL
)
from app.core.logger import logger

_access_token = None
_refresh_token = None
_token_expires_at = 0


def _token_al() -> dict:
    logger.info("Paraşüt'ten yeni token alınıyor...")
    response = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type": "password",
            "client_id": PARASUT_CLIENT_ID,
            "client_secret": PARASUT_CLIENT_SECRET,
            "username": PARASUT_USERNAME,
            "password": PARASUT_PASSWORD,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Paraşüt token hatası: {response.status_code}")
    logger.info("✅ Paraşüt token alındı")
    return response.json()


def _token_yenile(refresh_token: str) -> dict:
    logger.info("Paraşüt token yenileniyor...")
    response = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": PARASUT_CLIENT_ID,
            "client_secret": PARASUT_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"
        }
    )
    if response.status_code != 200:
        return _token_al()
    logger.info("✅ Paraşüt token yenilendi")
    return response.json()


def get_token() -> str:
    global _access_token, _refresh_token, _token_expires_at
    if _access_token is None:
        veri = _token_al()
    elif time.time() > _token_expires_at - 60:
        veri = _token_yenile(_refresh_token)
    else:
        return _access_token
    _access_token = veri["access_token"]
    _refresh_token = veri["refresh_token"]
    _token_expires_at = time.time() + veri["expires_in"]
    return _access_token


def parasut_get(endpoint: str, params: dict = None) -> dict:
    token = get_token()
    url = f"{PARASUT_BASE_URL}{endpoint}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params=params
    )
    if response.status_code != 200:
        raise Exception(f"Paraşüt API hatası: {response.status_code} — {response.text[:200]}")
    return response.json()


def parasut_post(endpoint: str, veri: dict) -> dict:
    token = get_token()
    url = f"{PARASUT_BASE_URL}{endpoint}"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"},
        json=veri
    )
    if response.status_code not in [200, 201]:
        raise Exception(f"Paraşüt API hatası: {response.status_code} — {response.text[:200]}")
    return response.json()


# ── CHATBOT SORGU FONKSİYONLARI ──────────────────────────────────────

def cari_listesi_getir() -> list:
    """Tüm carileri döndürür."""
    try:
        sonuc = parasut_get(f"/{PARASUT_COMPANY_ID}/contacts", params={"page[size]": 25})
        cariler = []
        for c in sonuc.get("data", []):
            attr = c.get("attributes", {})
            cariler.append({
                "id": c["id"],
                "ad": attr.get("name", "-"),
                "email": attr.get("email", "-"),
                "telefon": attr.get("phone", "-"),
                "bakiye": attr.get("balance", 0),
                "vergi_no": attr.get("tax_number", "-"),
            })
        return cariler
    except Exception as e:
        logger.error(f"Cari listesi hatası: {e}")
        return []


def cari_detay_getir(cari_id: str) -> dict:
    """Belirli bir carinin detaylarını döndürür."""
    try:
        sonuc = parasut_get(f"/{PARASUT_COMPANY_ID}/contacts/{cari_id}")
        attr = sonuc.get("data", {}).get("attributes", {})
        return {
            "id": cari_id,
            "ad": attr.get("name", "-"),
            "email": attr.get("email", "-"),
            "telefon": attr.get("phone", "-"),
            "adres": attr.get("address", "-"),
            "vergi_no": attr.get("tax_number", "-"),
            "vergi_dairesi": attr.get("tax_office", "-"),
            "bakiye": attr.get("balance", 0),
        }
    except Exception as e:
        logger.error(f"Cari detay hatası: {e}")
        return {}


def cari_faturalari_getir(cari_id: str, limit: int = 10) -> list:
    """Belirli bir carinin faturalarını döndürür."""
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/sales_invoices",
            params={"filter[contact_id]": cari_id, "page[size]": limit, "sort": "-issue_date"}
        )
        faturalar = []
        for f in sonuc.get("data", []):
            attr = f.get("attributes", {})
            faturalar.append({
                "id": f["id"],
                "tarih": attr.get("issue_date", "-"),
                "tutar": attr.get("gross_total", 0),
                "durum": attr.get("payment_status", "-"),
                "aciklama": attr.get("description", "-"),
            })
        return faturalar
    except Exception as e:
        logger.error(f"Cari fatura hatası: {e}")
        return []


def cari_irsaliyeleri_getir(cari_id: str, limit: int = 10) -> list:
    """Belirli bir carinin irsaliyelerini döndürür."""
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/shipment_documents",
            params={"filter[contact_id]": cari_id, "page[size]": limit, "sort": "-issue_date"}
        )
        irsaliyeler = []
        for i in sonuc.get("data", []):
            attr = i.get("attributes", {})
            irsaliyeler.append({
                "id": i["id"],
                "tarih": attr.get("issue_date", "-"),
                "aciklama": attr.get("description", "-"),
                "durum": attr.get("status", "-"),
            })
        return irsaliyeler
    except Exception as e:
        logger.error(f"Cari irsaliye hatası: {e}")
        return []


def acik_faturalar_getir() -> list:
    """Tüm açık (ödenmemiş) faturaları döndürür."""
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/sales_invoices",
            params={"filter[payment_status]": "overdue,not_paid", "page[size]": 25, "sort": "-issue_date"}
        )
        faturalar = []
        for f in sonuc.get("data", []):
            attr = f.get("attributes", {})
            faturalar.append({
                "id": f["id"],
                "tarih": attr.get("issue_date", "-"),
                "vade": attr.get("due_date", "-"),
                "tutar": attr.get("gross_total", 0),
                "kalan": attr.get("remaining", 0),
                "aciklama": attr.get("description", "-"),
            })
        return faturalar
    except Exception as e:
        logger.error(f"Açık fatura hatası: {e}")
        return []


def hesap_ozeti_getir() -> dict:
    """Genel hesap özetini döndürür."""
    try:
        cariler = cari_listesi_getir()
        toplam_alacak = sum(c.get("bakiye", 0) for c in cariler if (c.get("bakiye") or 0) > 0)
        toplam_borclu = sum(abs(c.get("bakiye", 0)) for c in cariler if (c.get("bakiye") or 0) < 0)
        return {
            "toplam_cari": len(cariler),
            "toplam_alacak": toplam_alacak,
            "toplam_borclu": toplam_borclu,
            "cariler": cariler
        }
    except Exception as e:
        logger.error(f"Hesap özeti hatası: {e}")
        return {}