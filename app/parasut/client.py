# client.py — Paraşüt API İstemcisi
#
# ASYNC KULLANIM NOTU:
# Bu modüldeki tüm fonksiyonlar senkron (requests kütüphanesi).
# FastAPI async endpoint'lerinden çağırırken dosyanın sonundaki
# async_* wrapper'ları kullan:
#
#   cariler = await async_cari_listesi_getir(tenant_id=tenant_id)
#   sonuc   = await async_parasut_post(endpoint, veri, tenant_id=tenant_id)
#
# Bu wrapper'lar asyncio.to_thread() kullanır — event loop bloklanmaz.
#
# MULTI-TENANT:
# Tüm fonksiyonlar tenant_id=None parametresi alır.
# tenant_id=None → DEFAULT_TENANT_ID (config.py'deki PARASUT_USERNAME).
# Her tenant'ın token ve cari cache'i tamamen izole tutulur.

import re
import difflib
import asyncio
import requests
import time
import threading
from app.core.config import (
    PARASUT_CLIENT_ID,
    PARASUT_CLIENT_SECRET,
    PARASUT_USERNAME,
    PARASUT_PASSWORD,
    PARASUT_COMPANY_ID,
    PARASUT_HURDA_PRODUCT_ID,
    PARASUT_BASE_URL,
)
from app.core.logger import logger

# ── PER-TENANT STATE ──────────────────────────────────────────────────
# {tid: {"access": str, "refresh": str, "expires_at": float}}
_tokens: dict = {}
_token_locks: dict = {}

# {tid: list}
_cari_caches: dict = {}
_cari_cache_times: dict = {}
_cache_locks: dict = {}

# {tid: str | None}
_kasa_hesabi_ids: dict = {}

# {tid: float}  — son istek zamanı (rate limiting)
_rate_limit_times: dict = {}

CACHE_SURE = 900       # 15 dakika
MIN_ISTEK_ARASI = 0.35

_meta_lock = threading.Lock()  # lock dict'lerine güvenli erişim için


def _resolve_tid(tenant_id) -> str:
    """tenant_id=None → DEFAULT_TENANT_ID."""
    if tenant_id:
        return str(tenant_id)
    return PARASUT_USERNAME or "__default__"


def _get_lock(locks_dict: dict, tid: str) -> threading.Lock:
    if tid not in locks_dict:
        with _meta_lock:
            if tid not in locks_dict:
                locks_dict[tid] = threading.Lock()
    return locks_dict[tid]


def _tenant_credentials(tid: str) -> dict:
    """
    Tenant credentials döner.
    DEFAULT tenant → global config.
    Diğerleri → DB'den Tenant kaydı.
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


# ── RATE LIMIT ────────────────────────────────────────────────────────

def _rate_limit_bekle(tid: str):
    son = _rate_limit_times.get(tid, 0)
    gecen = time.time() - son
    if gecen < MIN_ISTEK_ARASI:
        time.sleep(MIN_ISTEK_ARASI - gecen)
    _rate_limit_times[tid] = time.time()


# ── TOKEN YÖNETİMİ ────────────────────────────────────────────────────

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


# ── HTTP İSTEKLERİ ────────────────────────────────────────────────────

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


# ── CARİ CACHE ────────────────────────────────────────────────────────

def _cari_listesi_api(tenant_id=None) -> list:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    cariler = []
    page = 1
    while page <= 100:
        sonuc = parasut_get(
            f"/{cid}/contacts",
            params={"page[size]": 25, "page[number]": page},
            tenant_id=tid,
        )
        data = sonuc.get("data", [])
        if not data:
            break
        for c in data:
            attr = c.get("attributes", {})
            try:
                bakiye_raw = attr.get("balance")
                bakiye = float(bakiye_raw) if bakiye_raw is not None and bakiye_raw != "" else 0.0
            except (ValueError, TypeError):
                bakiye = 0.0
            cariler.append({
                "id":             c["id"],
                "ad":             attr.get("name", "-"),
                "email":          attr.get("email", "-"),
                "telefon":        attr.get("phone", "-"),
                "bakiye":         bakiye,
                "vergi_no":       attr.get("tax_number", "-"),
                "adres":          attr.get("address", "-"),
                "vergi_dairesi":  attr.get("tax_office", "-"),
                "account_type":   attr.get("account_type", "customer"),
            })
        if len(data) < 25:
            break
        page += 1
        time.sleep(0.1)
    logger.info(f"Paraşüt'ten {len(cariler)} cari çekildi (tenant={tid}, {page - 1} sayfa)")
    return cariler


def cari_cache_yenile(tenant_id=None) -> list:
    tid = _resolve_tid(tenant_id)
    yeni_liste = _cari_listesi_api(tid)
    lock = _get_lock(_cache_locks, tid)
    with lock:
        _cari_caches[tid] = yeni_liste
        _cari_cache_times[tid] = time.time()
    return _cari_caches[tid]


def cari_listesi_getir(force_refresh: bool = False, tenant_id=None) -> list:
    tid = _resolve_tid(tenant_id)

    # Kilitsiz hızlı okuma
    if not force_refresh and _cari_caches.get(tid) and time.time() - _cari_cache_times.get(tid, 0) < CACHE_SURE:
        return _cari_caches[tid]

    lock = _get_lock(_cache_locks, tid)
    with lock:
        if not force_refresh and _cari_caches.get(tid) and time.time() - _cari_cache_times.get(tid, 0) < CACHE_SURE:
            return _cari_caches[tid]
        try:
            _cari_caches[tid] = _cari_listesi_api(tid)
            _cari_cache_times[tid] = time.time()
            return _cari_caches[tid]
        except Exception as e:
            logger.error(f"Cari cache yenileme hatası (tenant={tid}): {e}")
            return _cari_caches.get(tid) or []


# ── CARİ EŞLEŞTIRME — Fuzzy Matching ─────────────────────────────────

_GEREKSIZ_KELIMELER = {"A.S", "A.Ş", "LTD", "ŞTİ", "STI", "SAN", "TIC", "TİC", "VE", "GERI", "DONUSUM", "AS"}
_FUZZY_ESIK = 0.72


def _normalize_cari_adi(ad: str) -> str:
    ad = ad.upper().strip()
    ad = re.sub(r'[().،,]', ' ', ad)
    kelimeler = [k for k in ad.split() if k not in _GEREKSIZ_KELIMELER]
    return " ".join(kelimeler)


def _fuzzy_skor(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def cari_id_bul(firma_adi: str, tenant_id=None) -> str | None:
    if not firma_adi:
        return None
    tid = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid = creds["company_id"]

    firma_upper = firma_adi.upper().strip()
    firma_norm  = _normalize_cari_adi(firma_adi)
    cariler     = cari_listesi_getir(tenant_id=tid)

    for c in cariler:
        if c["ad"].upper() == firma_upper:
            logger.info(f"Cari tam eşleşme (cache): {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    try:
        kelimeler = [k for k in firma_norm.split() if k not in _GEREKSIZ_KELIMELER]
        arama = " ".join(kelimeler[:2]) if kelimeler else firma_adi
        sonuc = parasut_get(
            f"/{cid}/contacts",
            params={"filter[name]": arama, "page[size]": 25},
            tenant_id=tid,
        )
        api_cariler = sonuc.get("data", [])
        for item in api_cariler:
            ad = item.get("attributes", {}).get("name", "")
            cari_id = item.get("id")
            if ad.upper() == firma_upper:
                logger.info(f"Cari tam eşleşme (API): {firma_adi} → {ad} (ID: {cari_id})")
                return cari_id
        if api_cariler:
            item = api_cariler[0]
            ad = item.get("attributes", {}).get("name", "")
            cari_id = item.get("id")
            logger.info(f"Cari API ilk sonuç: {firma_adi} → {ad} (ID: {cari_id})")
            return cari_id
    except Exception as e:
        logger.warning(f"API cari arama hatası (tenant={tid}): {e}")

    for c in cariler:
        cari_upper = c["ad"].upper()
        if firma_upper in cari_upper or cari_upper in firma_upper:
            logger.info(f"Cari içerik eşleşme (cache): {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    firma_kelimeler = set(firma_norm.split())
    for c in cariler:
        cari_kelimeler = set(_normalize_cari_adi(c["ad"]).split())
        anlamli_ortak  = firma_kelimeler & cari_kelimeler - _GEREKSIZ_KELIMELER
        if len(anlamli_ortak) >= 2:
            logger.info(f"Cari kelime eşleşme (cache): {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    en_iyi_skor  = 0.0
    en_iyi_cari  = None
    for c in cariler:
        skor = _fuzzy_skor(firma_norm, _normalize_cari_adi(c["ad"]))
        if skor > en_iyi_skor:
            en_iyi_skor = skor
            en_iyi_cari = c

    if en_iyi_cari and en_iyi_skor >= _FUZZY_ESIK:
        logger.info(
            f"Cari fuzzy eşleşme (skor={en_iyi_skor:.2f}): "
            f"{firma_adi} → {en_iyi_cari['ad']} (ID: {en_iyi_cari['id']})"
        )
        return en_iyi_cari["id"]

    logger.warning(f"Cari bulunamadı: {firma_adi} (en iyi fuzzy skor: {en_iyi_skor:.2f}, tenant={tid})")
    return None


# ── SORGU FONKSİYONLARI ───────────────────────────────────────────────

def cari_detay_getir(cari_id: str, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(f"/{cid}/contacts/{cari_id}", tenant_id=tid)
        attr = sonuc.get("data", {}).get("attributes", {})
        try:
            bakiye_raw = attr.get("balance")
            bakiye = float(bakiye_raw) if bakiye_raw is not None and bakiye_raw != "" else 0.0
        except (ValueError, TypeError):
            bakiye = 0.0
        return {
            "id":             cari_id,
            "ad":             attr.get("name", "-"),
            "email":          attr.get("email", "-"),
            "telefon":        attr.get("phone", "-"),
            "adres":          attr.get("address", "-"),
            "vergi_no":       attr.get("tax_number", "-"),
            "vergi_dairesi":  attr.get("tax_office", "-"),
            "bakiye":         bakiye,
        }
    except Exception as e:
        logger.error(f"Cari detay hatası: {e}")
        return {}


def cari_faturalari_getir(
        cari_id: str,
        limit: int = 25,
        sayfa: int = 1,
        baslangic: str = None,
        bitis: str = None,
        durum: str = None,
        tenant_id=None,
) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        tum_faturalar = []
        page = 1
        while page <= 60:
            params = {
                "filter[contact_id]": cari_id,
                "page[size]":         25,
                "page[number]":       page,
                "sort":               "-issue_date",
                "include":            "contact",
            }
            if durum:
                params["filter[payment_status]"] = durum

            sonuc = parasut_get(f"/{cid}/sales_invoices", params=params, tenant_id=tid)
            data  = sonuc.get("data", [])
            if not data:
                break

            for f in data:
                attr  = f.get("attributes", {})
                tarih = attr.get("issue_date", "")
                if baslangic and tarih < baslangic:
                    continue
                if bitis and tarih > bitis:
                    continue
                try:
                    kalan = float(attr.get("remaining") or 0)
                except (ValueError, TypeError):
                    kalan = 0.0
                try:
                    tutar = float(attr.get("gross_total") or 0)
                except (ValueError, TypeError):
                    tutar = 0.0
                aciklama = attr.get("description") or ""
                plaka = None
                plaka_match = re.search(r'\b(\d{2}\s*[A-ZÇŞĞÜÖİ]{1,3}\s*\d{2,4})\b', aciklama)
                if plaka_match:
                    plaka = plaka_match.group(1).replace(" ", "")
                irsaliye_ids = [
                    d["id"] for d in f.get("relationships", {}).get("shipment_documents", {}).get("data", [])
                ]
                irsaliye_id = None
                m = re.search(r'[İI]rsaliye\s+(\d{8,})', aciklama)
                if m:
                    irsaliye_id = m.group(1)
                tum_faturalar.append({
                    "id":           f["id"],
                    "tarih":        tarih,
                    "vade":         attr.get("due_date", "-"),
                    "tutar":        tutar,
                    "kalan":        kalan,
                    "durum":        attr.get("payment_status", "-"),
                    "aciklama":     aciklama or "-",
                    "plaka":        plaka,
                    "irsaliye_id":  irsaliye_id or (irsaliye_ids[0] if irsaliye_ids else None),
                    "irsaliye_ids": irsaliye_ids,
                })

            if baslangic and data:
                en_eski = data[-1].get("attributes", {}).get("issue_date", "")
                if en_eski and en_eski < baslangic:
                    break
            if len(data) < 25:
                break
            page += 1

        toplam_kayit  = len(tum_faturalar)
        baslangic_idx = (sayfa - 1) * min(limit, 25)
        bitis_idx     = baslangic_idx + min(limit, 25)
        sayfa_faturalari = tum_faturalar[baslangic_idx:bitis_idx]
        return {
            "faturalar":         sayfa_faturalari,
            "toplam":            len(sayfa_faturalari),
            "toplam_kayit":      toplam_kayit,
            "sayfa":             sayfa,
            "sonraki_sayfa_var": toplam_kayit > bitis_idx,
        }
    except Exception as e:
        logger.error(f"Cari fatura hatası: {e}")
        return {"faturalar": [], "toplam": 0, "toplam_kayit": 0, "sayfa": sayfa, "sonraki_sayfa_var": False}


def fatura_detay_getir(fatura_id: str, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc    = parasut_get(
            f"/{cid}/sales_invoices/{fatura_id}",
            params={"include": "contact,details,details.product,payments"},
            tenant_id=tid,
        )
        data     = sonuc.get("data", {})
        attr     = data.get("attributes", {})
        included = sonuc.get("included", [])
        detail_ids = {d["id"] for d in data.get("relationships", {}).get("details", {}).get("data", [])}
        kalemler = []
        for inc in included:
            if inc.get("type") == "sales_invoice_details" and inc["id"] in detail_ids:
                a = inc.get("attributes", {})
                try:
                    miktar      = float(a.get("quantity") or 0)
                    birim_fiyat = float(a.get("unit_price") or 0)
                    tutar_kalem = miktar * birim_fiyat
                except (ValueError, TypeError):
                    miktar = birim_fiyat = tutar_kalem = 0.0
                kalemler.append({
                    "aciklama":   a.get("description") or "Hurda",
                    "miktar":     miktar,
                    "birim_fiyat": birim_fiyat,
                    "tutar":      tutar_kalem,
                })
        payment_ids = {p["id"] for p in data.get("relationships", {}).get("payments", {}).get("data", [])}
        tahsilatlar = []
        for inc in included:
            if inc.get("type") == "payments" and inc["id"] in payment_ids:
                a = inc.get("attributes", {})
                tahsilatlar.append({
                    "tarih":    a.get("date", "-"),
                    "tutar":    float(a.get("amount", 0)),
                    "aciklama": a.get("description", "-"),
                })
        irsaliye_ids = [d["id"] for d in data.get("relationships", {}).get("shipment_documents", {}).get("data", [])]
        try:
            tutar = float(attr.get("gross_total") or 0)
            kalan = float(attr.get("remaining") or 0)
        except (ValueError, TypeError):
            tutar = kalan = 0.0
        return {
            "id": fatura_id, "tarih": attr.get("issue_date", "-"), "vade": attr.get("due_date", "-"),
            "tutar": tutar, "kalan": kalan, "durum": attr.get("payment_status", "-"),
            "aciklama": attr.get("description", "-"), "kalemler": kalemler,
            "tahsilatlar": tahsilatlar, "irsaliye_ids": irsaliye_ids,
        }
    except Exception as e:
        logger.error(f"Fatura detay hatası: {e}")
        return {}


def cari_irsaliyeleri_getir(cari_id: str, limit: int = 10, tenant_id=None) -> list:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(
            f"/{cid}/shipment_documents",
            params={"filter[contact_id]": cari_id, "page[size]": limit, "sort": "-issue_date"},
            tenant_id=tid,
        )
        return [
            {"id": i["id"], "tarih": i.get("attributes", {}).get("issue_date", "-"),
             "aciklama": i.get("attributes", {}).get("description", "-"),
             "durum": i.get("attributes", {}).get("status", "-")}
            for i in sonuc.get("data", [])
        ]
    except Exception as e:
        logger.error(f"Cari irsaliye hatası: {e}")
        return []


def acik_faturalar_getir(tenant_id=None) -> list:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(
            f"/{cid}/sales_invoices",
            params={"filter[payment_status]": "overdue,not_paid", "page[size]": 25, "sort": "-issue_date"},
            tenant_id=tid,
        )
        faturalar = []
        for f in sonuc.get("data", []):
            attr     = f.get("attributes", {})
            aciklama = attr.get("description") or ""
            irsaliye_id = None
            m = re.search(r'[İI]rsaliye\s+(\d{8,})', aciklama)
            if m:
                irsaliye_id = m.group(1)
            faturalar.append({
                "id":          f["id"],
                "tarih":       attr.get("issue_date", "-"),
                "vade":        attr.get("due_date", "-"),
                "tutar":       attr.get("gross_total", 0),
                "kalan":       attr.get("remaining", 0),
                "aciklama":    aciklama or "-",
                "irsaliye_id": irsaliye_id,
            })
        return faturalar
    except Exception as e:
        logger.error(f"Açık fatura hatası: {e}")
        return []


def hesap_ozeti_getir(tenant_id=None) -> dict:
    tid = _resolve_tid(tenant_id)
    try:
        cariler         = cari_listesi_getir(tenant_id=tid)
        toplam_alacak   = sum(c["bakiye"] for c in cariler if c["bakiye"] > 0)
        toplam_odeyecek = sum(abs(c["bakiye"]) for c in cariler if c["bakiye"] < 0)
        return {
            "toplam_cari":   len(cariler),
            "toplam_alacak": toplam_alacak,
            "toplam_borclu": toplam_odeyecek,
        }
    except Exception as e:
        logger.error(f"Hesap özeti hatası: {e}")
        return {}


def islem_gecmisi_getir(cari_id: str, baslangic: str = None, bitis: str = None, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        cari   = cari_detay_getir(cari_id, tenant_id=tid)
        bakiye = cari.get("bakiye", 0.0)
        islemler = []
        eklenen_tahsilat_ids = set()
        page = 1
        while page <= 50:
            fatura_sonuc = parasut_get(
                f"/{cid}/sales_invoices",
                params={"filter[contact_id]": cari_id, "include": "payments",
                        "page[size]": 25, "page[number]": page, "sort": "-issue_date"},
                tenant_id=tid,
            )
            faturalar = fatura_sonuc.get("data", [])
            included  = fatura_sonuc.get("included", [])
            payment_map = {item["id"]: item for item in included if item.get("type") == "payments"}

            for f in faturalar:
                attr  = f.get("attributes", {})
                tarih = attr.get("issue_date", "-")
                if not baslangic or tarih >= baslangic:
                    if not bitis or tarih <= bitis:
                        try:
                            tutar = float(attr.get("gross_total") or 0)
                        except (ValueError, TypeError):
                            tutar = 0.0
                        islemler.append({
                            "id": f.get("id"), "tarih": tarih, "tur": "sales_invoice",
                            "aciklama": attr.get("description") or "Satış Faturası",
                            "tutar": tutar, "durum": attr.get("payment_status", "-"),
                            "kalan": float(attr.get("remaining") or 0),
                        })
                for pid in [d["id"] for d in f.get("relationships", {}).get("payments", {}).get("data", [])]:
                    if pid in eklenen_tahsilat_ids:
                        continue
                    p = payment_map.get(pid)
                    if not p:
                        continue
                    eklenen_tahsilat_ids.add(pid)
                    pattr  = p.get("attributes", {})
                    ptarih = pattr.get("date", "-")
                    if baslangic and ptarih < baslangic:
                        continue
                    if bitis and ptarih > bitis:
                        continue
                    try:
                        ptutar = float(pattr.get("amount") or 0)
                    except (ValueError, TypeError):
                        ptutar = 0.0
                    islemler.append({
                        "id": pid, "tarih": ptarih, "tur": "payment",
                        "aciklama": pattr.get("notes") or pattr.get("description") or "Tahsilat",
                        "tutar": -ptutar, "durum": "paid", "kalan": 0,
                    })
            if len(faturalar) < 25:
                break
            page += 1

        islemler.sort(key=lambda x: x.get("tarih", ""), reverse=True)
        logger.info(f"İşlem geçmişi: cari_id={cari_id} toplam={len(islemler)} işlem (tenant={tid})")
        return {"cari_id": cari_id, "cari_adi": cari.get("ad", "-"), "bakiye": bakiye, "islemler": islemler}
    except Exception as e:
        logger.error(f"İşlem geçmişi hatası: {e}")
        return {"bakiye": 0, "islemler": []}


def cari_olustur(
        firma_adi: str,
        email: str = None,
        telefon: str = None,
        vergi_no: str = None,
        vergi_dairesi: str = None,
        adres: str = None,
        tenant_id=None,
) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        attributes = {"name": firma_adi, "contact_type": "company", "account_type": "customer"}
        if email:         attributes["email"] = email
        if telefon:       attributes["phone"] = telefon
        if vergi_no:      attributes["tax_number"] = vergi_no
        if vergi_dairesi: attributes["tax_office"] = vergi_dairesi
        if adres:         attributes["address"] = adres
        istek  = {"data": {"type": "contacts", "attributes": attributes}}
        sonuc  = parasut_post(f"/{cid}/contacts", istek, tenant_id=tid)
        cari_id = sonuc["data"]["id"]
        logger.info(f"Yeni cari oluşturuldu: {firma_adi} (ID: {cari_id}, tenant={tid})")
        cari_cache_yenile(tenant_id=tid)
        return {"basarili": True, "id": cari_id, "ad": firma_adi}
    except Exception as e:
        logger.error(f"Cari oluşturma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


# ── KASA HESABI ───────────────────────────────────────────────────────

def _kasa_hesabi_bul(tenant_id=None) -> str | None:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    if _kasa_hesabi_ids.get(tid):
        return _kasa_hesabi_ids[tid]
    try:
        sonuc = parasut_get(f"/{cid}/accounts", tenant_id=tid)
        for hesap in sonuc.get("data", []):
            attr = hesap.get("attributes", {})
            if attr.get("account_type") in ("cash", "bank"):
                logger.info(f"Kasa hesabı bulundu: {hesap['id']} ({attr.get('name')}) (tenant={tid})")
                _kasa_hesabi_ids[tid] = hesap["id"]
                return _kasa_hesabi_ids[tid]
        logger.warning(f"Kasa/banka hesabı bulunamadı (tenant={tid})")
        return None
    except Exception as e:
        logger.error(f"Kasa hesabı bulunamadı (tenant={tid}): {e}")
        return None


def fatura_tahsilat_ekle(
        fatura_id: str,
        tutar: float,
        aciklama: str = None,
        tarih: str = None,
        tenant_id=None,
) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        from datetime import datetime
        bugun    = tarih or datetime.now().strftime("%Y-%m-%d")
        hesap_id = _kasa_hesabi_bul(tenant_id=tid)
        if not hesap_id:
            return {"basarili": False, "hata": "Kasa/banka hesabı bulunamadı."}
        istek = {"data": {"type": "payments",
            "attributes": {"description": aciklama or "Tahsilat", "date": bugun, "amount": tutar},
            "relationships": {"account": {"data": {"type": "accounts", "id": str(hesap_id)}}},
        }}
        try:
            sonuc = parasut_post(f"/{cid}/sales_invoices/{fatura_id}/payments", istek, tenant_id=tid)
        except Exception as e:
            istek["data"]["attributes"]["amount"] = str(tutar)
            logger.warning(f"Tahsilat v2 deneniyor: {e}")
            sonuc = parasut_post(f"/{cid}/sales_invoices/{fatura_id}/payments", istek, tenant_id=tid)
        odeme_id = sonuc["data"]["id"]
        logger.info(f"Tahsilat eklendi: fatura={fatura_id} tutar={tutar:,.0f} TL (tenant={tid})")
        try:
            cari_cache_yenile(tenant_id=tid)
        except Exception:
            pass
        return {"basarili": True, "id": odeme_id, "fatura_id": fatura_id, "tutar": tutar, "tarih": bugun}
    except Exception as e:
        logger.error(f"Fatura tahsilat hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def tahsilat_ekle(
        cari_adi: str,
        tutar: float,
        aciklama: str = None,
        tarih: str = None,
        tenant_id=None,
) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        cari_id = cari_id_bul(cari_adi, tenant_id=tid)
        if not cari_id:
            return {"basarili": False, "hata": f"'{cari_adi}' adlı cari bulunamadı."}
        sonuc = parasut_get(
            f"/{cid}/sales_invoices",
            params={"filter[contact_id]": cari_id, "filter[payment_status]": "overdue,not_paid",
                    "page[size]": 25, "sort": "issue_date"},
            tenant_id=tid,
        )
        faturalar = []
        for f in sonuc.get("data", []):
            attr  = f.get("attributes", {})
            kalan = float(attr.get("remaining") or attr.get("gross_total") or 0)
            if kalan > 0:
                faturalar.append({"id": f["id"], "tarih": attr.get("issue_date", "-"),
                                   "tutar": float(attr.get("gross_total", 0)), "kalan": kalan})
        if not faturalar:
            return {"basarili": False, "hata": f"{cari_adi} için açık fatura bulunamadı."}

        kalan_tutar       = float(tutar)
        odenen_faturalar  = []
        toplam_odenen     = 0.0
        basarisiz_sayisi  = 0

        for fatura in faturalar:
            if kalan_tutar <= 0:
                break
            odeme_miktari = min(kalan_tutar, fatura["kalan"])
            if odeme_miktari <= 0:
                continue
            odeme_sonuc = fatura_tahsilat_ekle(
                fatura_id=fatura["id"], tutar=odeme_miktari,
                aciklama=aciklama or f"{cari_adi} tahsilat", tarih=tarih,
                tenant_id=tid,
            )
            if odeme_sonuc.get("basarili"):
                odenen_faturalar.append({"fatura_id": fatura["id"], "odenen": odeme_miktari})
                toplam_odenen += odeme_miktari
                kalan_tutar   -= odeme_miktari
                logger.info(f"Tahsilat: {cari_adi} — {odeme_miktari:,.0f} TL → fatura {fatura['id']}")
            else:
                basarisiz_sayisi += 1
                logger.warning(f"Fatura {fatura['id']} tahsilat başarısız: {odeme_sonuc.get('hata')}")

        if toplam_odenen > 0:
            return {
                "basarili": True, "cari": cari_adi, "tutar": toplam_odenen,
                "tarih": tarih or "", "odenen_faturalar": odenen_faturalar,
                "kalan_odenmedi": kalan_tutar if kalan_tutar > 0.01 else 0,
                "basarisiz_fatura": basarisiz_sayisi,
            }
        else:
            return {"basarili": False, "hata": "Hiçbir faturaya tahsilat eklenemedi."}
    except Exception as e:
        logger.error(f"Tahsilat ekleme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def tum_bekleyen_irsaliyeleri_getir(
        baslangic: str = None,
        bitis: str = None,
        sayfa: int = 1,
        sayfa_basi: int = 25,
        tenant_id=None,
) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        tum_irsaliyeler = []
        page = 1
        while page <= 50:
            params = {
                "page[size]":    25,
                "page[number]":  page,
                "sort":          "-issue_date",
                "include":       "stock_movements,contact",
            }
            sonuc    = parasut_get(f"/{cid}/shipment_documents", params=params, tenant_id=tid)
            data     = sonuc.get("data", [])
            if not data:
                break
            included = sonuc.get("included", [])
            sm_map   = {inc["id"]: inc.get("attributes", {}) for inc in included if inc.get("type") == "stock_movements"}
            contact_map = {inc["id"]: inc.get("attributes", {}).get("name", "-") for inc in included if inc.get("type") == "contacts"}

            for i in data:
                attr         = i.get("attributes", {})
                rels         = i.get("relationships", {})
                # Paraşüt'te bu irsaliye zaten bir faturaya bağlıysa atla
                if rels.get("sales_invoice", {}).get("data") or rels.get("purchase_bill", {}).get("data"):
                    continue
                contact_data = rels.get("contact", {}).get("data", {})
                contact_id   = contact_data.get("id") if contact_data else None
                if not contact_id:
                    continue
                tarih = attr.get("issue_date", "")
                if baslangic and tarih < baslangic:
                    continue
                if bitis and tarih > bitis:
                    continue

                sm_rels  = rels.get("stock_movements", {}).get("data", [])
                kalemler = []
                for sm_ref in sm_rels:
                    sm_attr = sm_map.get(sm_ref["id"], {})
                    miktar  = float(sm_attr.get("quantity", 0))
                    if miktar > 0:
                        desc      = sm_attr.get("description") or ""
                        detail_no = sm_attr.get("detail_no", len(kalemler) + 1)
                        malzeme_adi = f"Malzeme {detail_no}"
                        if desc:
                            try:
                                norm    = desc.replace(' - ', '—').replace('–', '—')
                                parcalar = [p.strip() for p in norm.split("—")]
                                if len(parcalar) >= 3 and "kg" in parcalar[-1].lower():
                                    malzeme_adi = parcalar[1].strip()
                                elif len(parcalar) == 2 and "kg" not in parcalar[1].lower():
                                    malzeme_adi = parcalar[1].strip()
                                elif len(parcalar) == 1 and parcalar[0]:
                                    malzeme_adi = parcalar[0].strip()
                            except Exception:
                                pass
                        kalemler.append({"aciklama": malzeme_adi, "miktar_kg": miktar})

                tum_irsaliyeler.append({
                    "id":         i["id"],
                    "contact_id": contact_id,
                    "cari_adi":   contact_map.get(contact_id, "-"),
                    "tarih":      tarih,
                    "aciklama":   attr.get("description", "-"),
                    "kalemler":   kalemler,
                    "toplam_kg":  sum(k["miktar_kg"] for k in kalemler),
                })

            if baslangic and data:
                en_eski_tarih = data[-1].get("attributes", {}).get("issue_date", "")
                if en_eski_tarih and en_eski_tarih < baslangic:
                    break
            if len(data) < 25:
                break
            page += 1

        toplam        = len(tum_irsaliyeler)
        baslangic_idx = (sayfa - 1) * sayfa_basi
        bitis_idx     = baslangic_idx + sayfa_basi
        sayfa_irsaliyeler = tum_irsaliyeler[baslangic_idx:bitis_idx]
        sonraki_sayfa_var = toplam > bitis_idx

        gruplu = {}
        for irs in sayfa_irsaliyeler:
            cid_k = irs["contact_id"]
            if cid_k not in gruplu:
                gruplu[cid_k] = []
            gruplu[cid_k].append(irs)

        logger.info(f"Bekleyen irsaliyeler: {toplam} toplam, sayfa={sayfa}, bu sayfada={len(sayfa_irsaliyeler)} (tenant={tid})")
        return {
            "irsaliyeler":       gruplu,
            "sonraki_sayfa_var": sonraki_sayfa_var,
            "sayfa":             sayfa,
            "toplam":            toplam,
        }
    except Exception as e:
        logger.error(f"Tüm bekleyen irsaliye hatası: {e}")
        return {"irsaliyeler": {}, "sonraki_sayfa_var": False, "sayfa": sayfa, "toplam": 0}


def cari_bekleyen_irsaliyeleri_getir(cari_adi: str, tenant_id=None) -> list:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        c_id = cari_id_bul(cari_adi, tenant_id=tid)
        if not c_id:
            return []
        sonuc    = parasut_get(
            f"/{cid}/shipment_documents",
            params={"filter[contact_id]": c_id, "page[size]": 25, "sort": "-issue_date",
                    "include": "stock_movements"},
            tenant_id=tid,
        )
        included = sonuc.get("included", [])
        sm_map   = {inc["id"]: inc.get("attributes", {}) for inc in included if inc.get("type") == "stock_movements"}
        irsaliyeler = []
        for i in sonuc.get("data", []):
            attr    = i.get("attributes", {})
            rels    = i.get("relationships", {})
            sm_rels = rels.get("stock_movements", {}).get("data", [])
            kalemler = []
            for sm_ref in sm_rels:
                sm_attr = sm_map.get(sm_ref["id"], {})
                miktar  = float(sm_attr.get("quantity", 0))
                if miktar > 0:
                    desc      = sm_attr.get("description") or ""
                    detail_no = sm_attr.get("detail_no", len(kalemler) + 1)
                    malzeme_adi = f"Malzeme {detail_no}"
                    if desc:
                        try:
                            norm    = desc.replace(' - ', '—').replace('–', '—')
                            parcalar = [p.strip() for p in norm.split("—")]
                            if len(parcalar) >= 3 and "kg" in parcalar[-1].lower():
                                malzeme_adi = parcalar[1].strip()
                            elif len(parcalar) == 2 and "kg" not in parcalar[1].lower():
                                malzeme_adi = parcalar[1].strip()
                            elif len(parcalar) == 1 and parcalar[0]:
                                malzeme_adi = parcalar[0].strip()
                        except Exception:
                            pass
                    kalemler.append({"aciklama": malzeme_adi, "miktar_kg": miktar})
            irsaliyeler.append({
                "id":       i["id"],
                "tarih":    attr.get("issue_date", "-"),
                "aciklama": attr.get("description", "-"),
                "kalemler": kalemler,
                "toplam_kg": sum(k["miktar_kg"] for k in kalemler),
            })
        logger.info(f"{cari_adi} için {len(irsaliyeler)} irsaliye bulundu (tenant={tid})")
        return irsaliyeler
    except Exception as e:
        logger.error(f"Bekleyen irsaliye hatası: {e}")
        return []


def irsaliye_detay_getir(irsaliye_id: str, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc    = parasut_get(
            f"/{cid}/shipment_documents/{irsaliye_id}",
            params={"include": "stock_movements,stock_movements.product"},
            tenant_id=tid,
        )
        data     = sonuc.get("data", {})
        attr     = data.get("attributes", {})
        included = sonuc.get("included", [])
        sm_rels  = data.get("relationships", {}).get("stock_movements", {}).get("data", [])
        sm_ids   = {sm["id"] for sm in sm_rels}
        kalemler = []
        for inc in included:
            if inc.get("type") == "stock_movements" and inc["id"] in sm_ids:
                sm_attr = inc.get("attributes", {})
                miktar  = float(sm_attr.get("quantity", 0))
                if miktar > 0:
                    kalemler.append({"aciklama": sm_attr.get("description") or "Hurda", "miktar_kg": miktar})
        return {
            "id":        irsaliye_id,
            "tarih":     attr.get("issue_date", "-"),
            "aciklama":  attr.get("description", "-"),
            "kalemler":  kalemler,
            "toplam_kg": sum(k["miktar_kg"] for k in kalemler),
        }
    except Exception as e:
        logger.error(f"İrsaliye detay hatası: {e}")
        return {}


def hesap_listesi_getir(tenant_id=None) -> list:
    """Tüm kasa ve banka hesaplarını döner."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(f"/{cid}/accounts", tenant_id=tid)
        hesaplar = []
        for h in sonuc.get("data", []):
            attr = h.get("attributes", {})
            hesaplar.append({
                "id":           h["id"],
                "name":         attr.get("name", "-"),
                "account_type": attr.get("account_type", "-"),
                "currency":     attr.get("currency", "TRL"),
                "balance":      float(attr.get("balance", 0) or 0),
            })
        return hesaplar
    except Exception as e:
        logger.error(f"Hesap listesi hatası: {e}")
        return []


def son_alis_faturalari_getir(limit: int = 30, tenant_id=None) -> list:
    """Ödenmemiş / son alış faturalarını döner."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(
            f"/{cid}/purchase_bills",
            params={"page[size]": 25, "sort": "-issue_date", "include": "supplier"},
            tenant_id=tid,
        )
        # included'dan id→ad haritası kur (include=supplier ile gelir)
        id_to_ad = {}
        for inc in sonuc.get("included", []):
            inc_id = str(inc.get("id", ""))
            inc_ad = inc.get("attributes", {}).get("name", "")
            if inc_id and inc_ad:
                id_to_ad[inc_id] = inc_ad

        # Cari cache ile de zenginleştir (boşlukları doldur)
        for c in (_cari_caches.get(tid) or []):
            cid_str = str(c.get("id", ""))
            if cid_str and cid_str not in id_to_ad:
                id_to_ad[cid_str] = c.get("ad", "-")

        faturalar = []
        for f in sonuc.get("data", []):
            attr = f.get("attributes", {})
            kalan = float(attr.get("remaining", 0) or 0)
            if kalan <= 0:
                continue
            contact_name = "-"
            try:
                rels = f.get("relationships", {})
                for rel_key in ("supplier", "contact", "spender"):
                    rel_obj = rels.get(rel_key, {})
                    rel_data = rel_obj.get("data") if isinstance(rel_obj, dict) else None
                    if rel_data and isinstance(rel_data, dict):
                        sid = str(rel_data.get("id", ""))
                        if sid:
                            contact_name = id_to_ad.get(sid, "-")
                            if contact_name != "-":
                                break
            except Exception:
                pass
            aciklama = attr.get("description", "") or ""
            goruntu_adi = contact_name if contact_name != "-" else (aciklama[:30] if aciklama else f["id"])
            faturalar.append({
                "id":       f["id"],
                "cari":     goruntu_adi,
                "tarih":    attr.get("issue_date", "-"),
                "tutar":    float(attr.get("gross_total", 0) or 0),
                "kalan":    kalan,
                "aciklama": aciklama,
            })
            if len(faturalar) >= limit:
                break
        return faturalar
    except Exception as e:
        logger.error(f"Alış fatura listesi hatası: {e}")
        return []


def alıs_faturasi_odeme_ekle(
        fatura_id: str,
        tutar: float,
        kasa_id: str = None,
        tarih: str = None,
        notlar: str = None,
        tenant_id=None,
) -> dict:
    """Alış faturasına (purchase_bill) ödeme ekler."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        from datetime import datetime
        bugun    = tarih or datetime.now().strftime("%Y-%m-%d")
        hesap_id = kasa_id or _kasa_hesabi_bul(tenant_id=tid)
        if not hesap_id:
            return {"basarili": False, "hata": "Kasa/banka hesabı bulunamadı."}
        istek = {"data": {"type": "payments",
            "attributes": {"description": notlar or "Hurda alım ödemesi", "date": bugun, "amount": tutar},
            "relationships": {"account": {"data": {"type": "accounts", "id": str(hesap_id)}}},
        }}
        try:
            sonuc = parasut_post(f"/{cid}/purchase_bills/{fatura_id}/payments", istek, tenant_id=tid)
        except Exception as e:
            istek["data"]["attributes"]["amount"] = str(tutar)
            logger.warning(f"Ödeme v2 deneniyor: {e}")
            sonuc = parasut_post(f"/{cid}/purchase_bills/{fatura_id}/payments", istek, tenant_id=tid)
        odeme_id = sonuc["data"]["id"]
        logger.info(f"Alış faturası ödeme eklendi: fatura={fatura_id} tutar={tutar:,.0f} TL (tenant={tid})")
        return {"basarili": True, "id": odeme_id, "fatura_id": fatura_id, "tutar": tutar, "tarih": bugun}
    except Exception as e:
        logger.error(f"Alış faturası ödeme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def stok_hareketleri_getir(urun_id: str = None, limit: int = 50, tenant_id=None) -> list:
    """Stok giriş/çıkış hareketlerini döner. urun_id ile filtrelenebilir."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        params = {"page[size]": min(limit, 25), "sort": "-date"}
        if urun_id:
            params["filter[product_id]"] = urun_id
        sonuc    = parasut_get(f"/{cid}/stock_movements", params=params, tenant_id=tid)
        hareketler = []
        for h in sonuc.get("data", []):
            attr = h.get("attributes", {})
            hareketler.append({
                "id":          h["id"],
                "tarih":       (attr.get("date") or attr.get("created_at") or "-")[:10],
                "miktar":      float(attr.get("quantity", 0) or 0),
                "aciklama":    attr.get("description", "-"),
                "giris_mi":    attr.get("inflow", False),
            })
        logger.info(f"Stok hareketleri: {len(hareketler)} kayıt (tenant={tid})")
        return hareketler
    except Exception as e:
        logger.error(f"Stok hareketleri hatası: {e}")
        return []


def gelir_gider_raporu_getir(baslangic: str, bitis: str, tenant_id=None) -> dict:
    """
    Belirtilen tarih aralığı için gelir/gider raporu üretir.
    Paraşüt'te özel rapor endpoint'i olmadığından Python'da hesaplanır.
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    def _tum_sayfalari_cek(endpoint, extra_params, tid):
        """Paraşüt max page[size]=25 olduğundan tüm sayfaları döngüyle çeker."""
        tum_data = []
        page = 1
        while True:
            params = {"page[size]": 25, "page[number]": page, **extra_params}
            sonuc = parasut_get(endpoint, params=params, tenant_id=tid)
            data = sonuc.get("data", [])
            tum_data.extend(data)
            meta = sonuc.get("meta", {})
            total_pages = meta.get("total_pages", 1)
            if page >= total_pages or not data:
                break
            page += 1
        return tum_data

    try:
        # Satış faturaları (gelir)
        satis_data = _tum_sayfalari_cek(
            f"/{cid}/sales_invoices",
            {"sort": "-issue_date"},
            tid,
        )
        toplam_gelir  = 0.0
        toplam_gelir_kdv = 0.0
        satis_sayisi  = 0
        for f in satis_data:
            attr  = f.get("attributes", {})
            tarih = attr.get("issue_date", "")
            if baslangic and tarih < baslangic:
                continue
            if bitis and tarih > bitis:
                continue
            toplam_gelir     += float(attr.get("gross_total", 0) or 0)
            toplam_gelir_kdv += float(attr.get("total_vat", 0) or 0)
            satis_sayisi     += 1

        # Alış faturaları (gider)
        alis_data = _tum_sayfalari_cek(
            f"/{cid}/purchase_bills",
            {"sort": "-issue_date"},
            tid,
        )
        toplam_gider     = 0.0
        toplam_gider_kdv = 0.0
        alis_sayisi      = 0
        for f in alis_data:
            attr  = f.get("attributes", {})
            tarih = attr.get("issue_date", "")
            if baslangic and tarih < baslangic:
                continue
            if bitis and tarih > bitis:
                continue
            toplam_gider     += float(attr.get("gross_total", 0) or 0)
            toplam_gider_kdv += float(attr.get("total_vat", 0) or 0)
            alis_sayisi      += 1

        net_kar = toplam_gelir - toplam_gider
        logger.info(f"Gelir/gider raporu: gelir={toplam_gelir:,.0f} gider={toplam_gider:,.0f} (tenant={tid})")
        return {
            "baslangic":       baslangic,
            "bitis":           bitis,
            "toplam_gelir":    toplam_gelir,
            "gelir_kdv":       toplam_gelir_kdv,
            "satis_sayisi":    satis_sayisi,
            "toplam_gider":    toplam_gider,
            "gider_kdv":       toplam_gider_kdv,
            "alis_sayisi":     alis_sayisi,
            "net_kar":         net_kar,
            "kdv_beyan":       toplam_gelir_kdv - toplam_gider_kdv,
        }
    except Exception as e:
        logger.error(f"Gelir/gider raporu hatası: {e}")
        return {}


def para_transferi(
        kaynak_id: str,
        hedef_id: str,
        tutar: float,
        tarih: str = None,
        aciklama: str = None,
        tenant_id=None,
) -> dict:
    """Kasa/banka hesapları arasında para transferi yapar."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        from datetime import datetime
        bugun = tarih or datetime.now().strftime("%Y-%m-%d")
        not_  = aciklama or "Hesaplar arası transfer"

        # Kaynaktan çıkış (credit = bakiyeyi düşürür)
        parasut_post(f"/{cid}/accounts/{kaynak_id}/credit_transactions", {
            "data": {"type": "credit_transactions",
                     "attributes": {"description": not_, "amount": tutar, "date": bugun}}
        }, tenant_id=tid)

        # Hedefe giriş (debit = bakiyeyi artırır)
        parasut_post(f"/{cid}/accounts/{hedef_id}/debit_transactions", {
            "data": {"type": "debit_transactions",
                     "attributes": {"description": not_, "amount": tutar, "date": bugun}}
        }, tenant_id=tid)

        logger.info(f"Para transferi: {kaynak_id} → {hedef_id} | {tutar:,.0f} TL (tenant={tid})")
        return {"basarili": True, "tutar": tutar, "tarih": bugun, "kaynak_id": kaynak_id, "hedef_id": hedef_id}
    except Exception as e:
        logger.error(f"Para transferi hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def cari_tipi_bul(cari_adi: str, tenant_id=None) -> str:
    """
    Cari adından Paraşüt account_type döner.
    'supplier'  → tedarikçi (alış akışı: purchase_bills, gelen irsaliye)
    'customer'  → müşteri  (satış akışı: sales_invoices, giden irsaliye)
    Bulunamazsa 'customer' döner (güvenli varsayılan).
    """
    tid    = _resolve_tid(tenant_id)
    cariler = _cari_caches.get(tid) or []
    if not cariler:
        cariler = cari_listesi_getir(tenant_id=tid)

    cari_id = cari_id_bul(cari_adi, tenant_id=tid)
    if not cari_id:
        return "customer"

    for c in cariler:
        if str(c.get("id")) == str(cari_id):
            t = c.get("account_type", "customer")
            # 'both' → tedarikçi akışı (kantar fişi = gelen mal = alış)
            if t in ("supplier", "both"):
                return "supplier"
            return "customer"
    return "customer"


# ── ASYNC WRAPPER'LAR ─────────────────────────────────────────────────

async def async_cari_listesi_getir(force_refresh: bool = False, tenant_id=None) -> list:
    return await asyncio.to_thread(cari_listesi_getir, force_refresh, tenant_id)


async def async_cari_cache_yenile(tenant_id=None) -> list:
    return await asyncio.to_thread(cari_cache_yenile, tenant_id)


async def async_cari_id_bul(firma_adi: str, tenant_id=None) -> str | None:
    return await asyncio.to_thread(cari_id_bul, firma_adi, tenant_id)


async def async_parasut_post(endpoint: str, veri: dict, tenant_id=None) -> dict:
    return await asyncio.to_thread(parasut_post, endpoint, veri, tenant_id)


async def async_parasut_delete(endpoint: str, tenant_id=None) -> bool:
    return await asyncio.to_thread(parasut_delete, endpoint, tenant_id)


async def async_tum_bekleyen_irsaliyeleri_getir(
        baslangic: str = None, bitis: str = None,
        sayfa: int = 1, sayfa_basi: int = 25,
        tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        tum_bekleyen_irsaliyeleri_getir, baslangic, bitis, sayfa, sayfa_basi, tenant_id
    )


async def async_hesap_ozeti_getir(tenant_id=None) -> dict:
    return await asyncio.to_thread(hesap_ozeti_getir, tenant_id)


async def async_cari_detay_getir(cari_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(cari_detay_getir, cari_id, tenant_id)


async def async_islem_gecmisi_getir(
        cari_id: str, baslangic: str = None, bitis: str = None, tenant_id=None
) -> dict:
    return await asyncio.to_thread(islem_gecmisi_getir, cari_id, baslangic, bitis, tenant_id)


async def async_tahsilat_ekle(
        cari_adi: str, tutar: float,
        aciklama: str = None, tarih: str = None, tenant_id=None
) -> dict:
    return await asyncio.to_thread(tahsilat_ekle, cari_adi, tutar, aciklama, tarih, tenant_id)


async def async_cari_faturalari_getir(
        cari_id: str, limit: int = 25, sayfa: int = 1,
        baslangic: str = None, bitis: str = None, durum: str = None,
        tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        cari_faturalari_getir, cari_id, limit, sayfa, baslangic, bitis, durum, tenant_id
    )


async def async_fatura_detay_getir(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(fatura_detay_getir, fatura_id, tenant_id)


async def async_hesap_listesi_getir(tenant_id=None) -> list:
    return await asyncio.to_thread(hesap_listesi_getir, tenant_id)


async def async_cari_tipi_bul(cari_adi: str, tenant_id=None) -> str:
    return await asyncio.to_thread(cari_tipi_bul, cari_adi, tenant_id)


async def async_son_alis_faturalari_getir(limit: int = 30, tenant_id=None) -> list:
    return await asyncio.to_thread(son_alis_faturalari_getir, limit, tenant_id)


async def async_alıs_faturasi_odeme_ekle(
        fatura_id: str, tutar: float, kasa_id: str = None,
        tarih: str = None, notlar: str = None, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(alıs_faturasi_odeme_ekle, fatura_id, tutar, kasa_id, tarih, notlar, tenant_id)


async def async_stok_hareketleri_getir(urun_id: str = None, limit: int = 50, tenant_id=None) -> list:
    return await asyncio.to_thread(stok_hareketleri_getir, urun_id, limit, tenant_id)


async def async_gelir_gider_raporu_getir(baslangic: str, bitis: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(gelir_gider_raporu_getir, baslangic, bitis, tenant_id)


async def async_para_transferi(
        kaynak_id: str, hedef_id: str, tutar: float,
        tarih: str = None, aciklama: str = None, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(para_transferi, kaynak_id, hedef_id, tutar, tarih, aciklama, tenant_id)
