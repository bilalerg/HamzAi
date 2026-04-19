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

# ── TOKEN ─────────────────────────────────────────────────────────────
_access_token = None
_refresh_token = None
_token_expires_at = 0

# ── CARİ CACHE (in-memory, 30 dakika) ────────────────────────────────
_cari_cache = None
_cari_cache_time = 0
CACHE_SURE = 1800  # 30 dakika


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


# ── CARİ CACHE SİSTEMİ ───────────────────────────────────────────────

def _cari_listesi_api() -> list:
    """Paraşüt'ten tüm carileri çeker (tüm sayfalar)."""
    cariler = []
    page = 1
    while True:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/contacts",
            params={"page[size]": 25, "page[number]": page}
        )
        data = sonuc.get("data", [])
        if not data:
            break
        for c in data:
            attr = c.get("attributes", {})
            cariler.append({
                "id": c["id"],
                "ad": attr.get("name", "-"),
                "email": attr.get("email", "-"),
                "telefon": attr.get("phone", "-"),
                "bakiye": attr.get("balance", 0),
                "vergi_no": attr.get("tax_number", "-"),
                "adres": attr.get("address", "-"),
                "vergi_dairesi": attr.get("tax_office", "-"),
            })
        if len(data) < 25:
            break
        page += 1
    logger.info(f"✅ Paraşüt'ten {len(cariler)} cari çekildi")
    return cariler


def cari_cache_yenile():
    """Cache'i zorla yeniler."""
    global _cari_cache, _cari_cache_time
    _cari_cache = _cari_listesi_api()
    _cari_cache_time = time.time()
    return _cari_cache


def cari_listesi_getir() -> list:
    """Cache-aside pattern ile cari listesi döndürür."""
    global _cari_cache, _cari_cache_time
    # Cache geçerliyse direkt döndür
    if _cari_cache and time.time() - _cari_cache_time < CACHE_SURE:
        return _cari_cache
    # Cache miss — API'den çek
    try:
        return cari_cache_yenile()
    except Exception as e:
        logger.error(f"Cari cache yenileme hatası: {e}")
        return _cari_cache or []


def cari_id_bul(firma_adi: str) -> str | None:
    """
    Firma adından Paraşüt cari ID'sini dinamik olarak bulur.
    Config.py'de hardcode ID yok — Paraşüt'ten çeker, cache'te tutar.
    """
    if not firma_adi:
        return None

    firma_upper = firma_adi.upper().strip()
    cariler = cari_listesi_getir()

    # 1. Tam eşleşme
    for c in cariler:
        if c["ad"].upper() == firma_upper:
            logger.info(f"Cari tam eşleşme: {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    # 2. İçerik eşleşmesi
    for c in cariler:
        cari_upper = c["ad"].upper()
        if firma_upper in cari_upper or cari_upper in firma_upper:
            logger.info(f"Cari içerik eşleşme: {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    # 3. Kelime bazlı eşleşme (en az 2 kelime uyuşsun)
    firma_kelimeler = set(firma_upper.split())
    for c in cariler:
        cari_kelimeler = set(c["ad"].upper().split())
        ortak = firma_kelimeler & cari_kelimeler
        # "A.Ş", "LTD", "SAN" gibi genel kelimeleri say alma
        genel = {"A.Ş", "LTD", "ŞTİ", "SAN", "TİC", "VE", "GERİ", "DÖNÜŞÜM", "NAK", "TİCARET"}
        anlamli_ortak = ortak - genel
        if len(anlamli_ortak) >= 2:
            logger.info(f"Cari kelime eşleşme: {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    logger.warning(f"Cari bulunamadı: {firma_adi}")
    return None


# ── CHATBOT SORGU FONKSİYONLARI ──────────────────────────────────────

def cari_detay_getir(cari_id: str) -> dict:
    """Belirli bir carinin detaylarını döndürür."""
    try:
        # Önce cache'den bak
        cariler = cari_listesi_getir()
        for c in cariler:
            if c["id"] == str(cari_id):
                return c
        # Cache'de yoksa API'den çek
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


def cari_olustur(firma_adi: str, email: str = None, telefon: str = None,
                 vergi_no: str = None, vergi_dairesi: str = None, adres: str = None) -> dict:
    """Paraşüt'te yeni cari oluşturur ve cache'i yeniler."""
    try:
        attributes = {
            "name": firma_adi,
            "contact_type": "company",
            "account_type": "customer",
        }
        if email: attributes["email"] = email
        if telefon: attributes["phone"] = telefon
        if vergi_no: attributes["tax_number"] = vergi_no
        if vergi_dairesi: attributes["tax_office"] = vergi_dairesi
        if adres: attributes["address"] = adres

        istek = {"data": {"type": "contacts", "attributes": attributes}}
        sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/contacts", istek)
        cari_id = sonuc["data"]["id"]

        logger.info(f"✅ Yeni cari oluşturuldu: {firma_adi} (ID: {cari_id})")

        # Cache'i yenile — yeni cari hemen görünsün
        cari_cache_yenile()

        return {"basarili": True, "id": cari_id, "ad": firma_adi}
    except Exception as e:
        logger.error(f"Cari oluşturma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def tahsilat_ekle(cari_adi: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    """Paraşüt'te belirli bir cariye tahsilat kaydı ekler."""
    try:
        from datetime import datetime

        cari_id = cari_id_bul(cari_adi)
        if not cari_id:
            return {"basarili": False, "hata": f"'{cari_adi}' adlı cari bulunamadı."}

        bugun = tarih or datetime.now().strftime("%Y-%m-%d")

        istek = {
            "data": {
                "type": "payments",
                "attributes": {
                    "description": aciklama or f"{cari_adi} tahsilat",
                    "payment_date": bugun,
                    "amount": str(tutar),
                    "currency": "TRL",
                    "payment_type": "cash",
                    "payment_category": "income",
                },
                "relationships": {
                    "contact": {
                        "data": {"type": "contacts", "id": str(cari_id)}
                    }
                }
            }
        }

        sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/payments", istek)
        odeme_id = sonuc["data"]["id"]

        logger.info(f"✅ Tahsilat eklendi: {cari_adi} — {tutar:,.0f} TL (ID: {odeme_id})")
        cari_cache_yenile()

        return {
            "basarili": True,
            "id": odeme_id,
            "cari": cari_adi,
            "tutar": tutar,
            "tarih": bugun
        }
    except Exception as e:
        logger.error(f"Tahsilat ekleme hatası: {e}")
        return {"basarili": False, "hata": str(e)}