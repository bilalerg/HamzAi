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


# ── CARİ CACHE ────────────────────────────────────────────────────────

def _cari_sayfa_cek(cid: str, tid: str, tip_filtresi: str = None) -> list:
    """
    Paraşüt'ten tek bir tipte (customer / supplier) tüm carileri sayfalı çeker.
    tip_filtresi None ise filtre uygulanmaz (eski davranış).
    """
    cariler = []
    page = 1
    while page <= 100:
        params = {"page[size]": 25, "page[number]": page}
        if tip_filtresi:
            params["filter[account_type]"] = tip_filtresi
        sonuc = parasut_get(f"/{cid}/contacts", params=params, tenant_id=tid)
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
                "_raw_account_type": attr.get("account_type"),
            })
        if len(data) < 25:
            break
        page += 1
        time.sleep(0.1)
    return cariler


def _cari_listesi_api(tenant_id=None) -> list:
    """
    Cari listesini Paraşüt'ten çeker.

    KRİTİK: Paraşüt'te bir cari hem müşteri hem tedarikçi listesinde
    görünebilir. Tek listeyle çekersek (filter olmadan) bir rolü kaybederiz.
    Bu yüzden iki ayrı sorgu atıp ID üzerinden merge ediyoruz:
      - filter[account_type]=customer  → müşteri listesi
      - filter[account_type]=supplier → tedarikçi listesi
      - iki listede de aynı ID varsa → account_type='customer_supplier'
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]

    # İki ayrı liste — Paraşüt'ün filter[account_type] desteği ile
    musteriler = _cari_sayfa_cek(cid, tid, tip_filtresi="customer")
    tedarikciler = _cari_sayfa_cek(cid, tid, tip_filtresi="supplier")

    # ID bazlı merge — aynı ID iki listede de varsa 'customer_supplier'
    birlesik: dict = {}
    for c in musteriler:
        birlesik[c["id"]] = dict(c, account_type="customer")
    for c in tedarikciler:
        if c["id"] in birlesik:
            birlesik[c["id"]]["account_type"] = "customer_supplier"
        else:
            birlesik[c["id"]] = dict(c, account_type="supplier")

    # _raw_account_type alanını temizle (debug için kullandık, dışarı sızmasın)
    for c in birlesik.values():
        c.pop("_raw_account_type", None)

    cariler = list(birlesik.values())
    dagilim = {}
    for c in cariler:
        dagilim[c["account_type"]] = dagilim.get(c["account_type"], 0) + 1
    logger.info(
        f"Paraşüt'ten {len(cariler)} cari çekildi (tenant={tid}) | "
        f"müşteri sayfa: {len(musteriler)}, tedarikçi sayfa: {len(tedarikciler)} | "
        f"merged dağılım: {dagilim}"
    )
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


def _sales_faturalari_page(cid: str, tid: str, cari_id: str, durum: str, page: int) -> list:
    """Tek sayfa satış faturası çeker — cari_faturalari_getir için yardımcı."""
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
    return sonuc.get("data", [])


def _purchase_faturalari_page(cid: str, tid: str, cari_id: str, page: int) -> list:
    """Tek sayfa alış faturası çeker. Önce filter[supplier_id], fallback contact_id."""
    base = {
        "page[size]":   25,
        "page[number]": page,
        "sort":         "-issue_date",
        "include":      "supplier",
    }
    try:
        params = dict(base, **{"filter[supplier_id]": cari_id})
        sonuc = parasut_get(f"/{cid}/purchase_bills", params=params, tenant_id=tid)
        return sonuc.get("data", [])
    except Exception as e:
        logger.warning(f"purchase_bills filter[supplier_id] başarısız, contact_id fallback: {e}")
        try:
            params = dict(base, **{"filter[contact_id]": cari_id})
            sonuc = parasut_get(f"/{cid}/purchase_bills", params=params, tenant_id=tid)
            return sonuc.get("data", [])
        except Exception:
            return []


def _fatura_dict_olustur(f: dict, tip: str) -> dict:
    """Tek bir Paraşüt fatura JSON'unu UI'a uygun dict'e çevirir."""
    attr  = f.get("attributes", {})
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
    return {
        "id":           f["id"],
        "tip":          tip,                          # 'customer' (sales) / 'supplier' (purchase)
        "tarih":        attr.get("issue_date", ""),
        "vade":         attr.get("due_date", "-"),
        "tutar":        tutar,
        "kalan":        kalan,
        "durum":        attr.get("payment_status", "-"),
        "aciklama":     aciklama or "-",
        "plaka":        plaka,
        "irsaliye_id":  irsaliye_id or (irsaliye_ids[0] if irsaliye_ids else None),
        "irsaliye_ids": irsaliye_ids,
    }


def cari_faturalari_getir(
        cari_id: str,
        limit: int = 25,
        sayfa: int = 1,
        baslangic: str = None,
        bitis: str = None,
        durum: str = None,
        tip: str = "customer",
        tenant_id=None,
) -> dict:
    """
    Cari faturalarını listeler.
    tip:
      - 'customer'  → sales_invoices (müşteriye kestiğimiz satış faturaları)
      - 'supplier'  → purchase_bills (tedarikçiden aldığımız alış faturaları)
      - 'both'      → her ikisini birleştirip tarih sıralı döner
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    tip_temiz = tip if tip in ("customer", "supplier", "both") else "customer"
    try:
        tum_faturalar = []

        # ── SATIŞ FATURALARI (müşteri) ─────────────────────────────────
        if tip_temiz in ("customer", "both"):
            page = 1
            while page <= 60:
                data = _sales_faturalari_page(cid, tid, cari_id, durum, page)
                if not data:
                    break
                for f in data:
                    attr  = f.get("attributes", {})
                    tarih = attr.get("issue_date", "")
                    if baslangic and tarih < baslangic:
                        continue
                    if bitis and tarih > bitis:
                        continue
                    tum_faturalar.append(_fatura_dict_olustur(f, "customer"))
                if baslangic and data:
                    en_eski = data[-1].get("attributes", {}).get("issue_date", "")
                    if en_eski and en_eski < baslangic:
                        break
                if len(data) < 25:
                    break
                page += 1

        # ── ALIŞ FATURALARI (tedarikçi) ────────────────────────────────
        if tip_temiz in ("supplier", "both"):
            page = 1
            while page <= 60:
                data = _purchase_faturalari_page(cid, tid, cari_id, page)
                if not data:
                    break
                for f in data:
                    attr  = f.get("attributes", {})
                    tarih = attr.get("issue_date", "")
                    if baslangic and tarih < baslangic:
                        continue
                    if bitis and tarih > bitis:
                        continue
                    # durum filtresi (purchase_bills için): Python tarafında uygula
                    if durum and attr.get("payment_status") != durum:
                        continue
                    tum_faturalar.append(_fatura_dict_olustur(f, "supplier"))
                if baslangic and data:
                    en_eski = data[-1].get("attributes", {}).get("issue_date", "")
                    if en_eski and en_eski < baslangic:
                        break
                if len(data) < 25:
                    break
                page += 1

        # tarih sıralaması — birden fazla kaynak birleştiğinde gerekli
        if tip_temiz == "both":
            tum_faturalar.sort(key=lambda x: x.get("tarih", ""), reverse=True)

        toplam_kayit  = len(tum_faturalar)
        baslangic_idx = (sayfa - 1) * min(limit, 25)
        bitis_idx     = baslangic_idx + min(limit, 25)
        sayfa_faturalari = tum_faturalar[baslangic_idx:bitis_idx]
        return {
            "faturalar":         sayfa_faturalari,
            "toplam":            len(sayfa_faturalari),
            "toplam_kayit":      toplam_kayit,
            "sayfa":             sayfa,
            "tip":               tip_temiz,
            "sonraki_sayfa_var": toplam_kayit > bitis_idx,
        }
    except Exception as e:
        logger.error(f"Cari fatura hatası: {e}")
        return {"faturalar": [], "toplam": 0, "toplam_kayit": 0, "sayfa": sayfa,
                "tip": tip_temiz, "sonraki_sayfa_var": False}


def fatura_detay_getir(fatura_id: str, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        # NOT: Tahsilat silme için Paraşüt **transaction** id'sini ister
        # (payment.id değil). Bu yüzden include=payments.transaction ile
        # çekip ödeme satırına transaction_id'yi de iliştiriyoruz.
        sonuc    = parasut_get(
            f"/{cid}/sales_invoices/{fatura_id}",
            params={"include": "contact,details,details.product,payments,payments.transaction"},
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
                # payment → transaction ilişkisi (silme için gerekli)
                tx_rel = inc.get("relationships", {}).get("transaction", {}).get("data") or {}
                tx_id  = tx_rel.get("id") if isinstance(tx_rel, dict) else None
                tahsilatlar.append({
                    "id":              inc["id"],
                    "transaction_id":  tx_id,
                    "tarih":           a.get("date", "-"),
                    "tutar":           float(a.get("amount", 0)),
                    "aciklama":        a.get("description", "-"),
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
        faturalar = []
        for durum in ("overdue", "not_due", "unscheduled"):
            sonuc = parasut_get(
                f"/{cid}/sales_invoices",
                params={"filter[payment_status]": durum, "page[size]": 25, "sort": "-issue_date"},
                tenant_id=tid,
            )
            for f in sonuc.get("data", []):
                attr     = f.get("attributes", {})
                kalan    = float(attr.get("remaining") or 0)
                if kalan <= 0:
                    continue
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
                    "kalan":       kalan,
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
    """
    Carinin işlem geçmişi — hem satış (sales_invoices) hem alış (purchase_bills)
    + bağlı ödemeler. Müşteri/Tedarikçi ayrımı yapmadan iki kanaldan da çekilir;
    cari hangisiyse onlar dolu döner. Hurda sektöründe çoğu cari "both" tipinde.
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        cari   = cari_detay_getir(cari_id, tenant_id=tid)
        bakiye = cari.get("bakiye", 0.0)
        islemler = []
        eklenen_tahsilat_ids = set()

        # ── 1) SATIŞ FATURALARI + TAHSİLATLAR ──────────────────────────
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

        # ── 2) ALIŞ FATURALARI + ÖDEMELER (tedarikçi geçmişi) ──────────
        page = 1
        eklenen_odeme_ids = set()
        while page <= 50:
            try:
                pb_sonuc = parasut_get(
                    f"/{cid}/purchase_bills",
                    params={"filter[supplier_id]": cari_id, "include": "payments",
                            "page[size]": 25, "page[number]": page, "sort": "-issue_date"},
                    tenant_id=tid,
                )
            except Exception as e:
                # Bazı eski Paraşüt instansları filter[supplier_id] yerine
                # filter[contact_id] istiyor — fallback dene
                logger.warning(f"purchase_bills filter[supplier_id] başarısız, fallback: {e}")
                try:
                    pb_sonuc = parasut_get(
                        f"/{cid}/purchase_bills",
                        params={"filter[contact_id]": cari_id, "include": "payments",
                                "page[size]": 25, "page[number]": page, "sort": "-issue_date"},
                        tenant_id=tid,
                    )
                except Exception:
                    pb_sonuc = {"data": [], "included": []}
            alis_faturalar = pb_sonuc.get("data", [])
            pb_included    = pb_sonuc.get("included", [])
            pb_payment_map = {it["id"]: it for it in pb_included if it.get("type") == "payments"}

            for f in alis_faturalar:
                attr  = f.get("attributes", {})
                tarih = attr.get("issue_date", "-")
                if baslangic and tarih < baslangic:
                    continue
                if bitis and tarih > bitis:
                    continue
                try:
                    tutar = float(attr.get("gross_total") or 0)
                except (ValueError, TypeError):
                    tutar = 0.0
                islemler.append({
                    "id": f.get("id"), "tarih": tarih, "tur": "purchase_bill",
                    "aciklama": attr.get("description") or "Alış Faturası",
                    # Alış faturası bizim için borç (-)
                    "tutar": -tutar, "durum": attr.get("payment_status", "-"),
                    "kalan": float(attr.get("remaining") or 0),
                })
                for pid in [d["id"] for d in f.get("relationships", {}).get("payments", {}).get("data", [])]:
                    if pid in eklenen_odeme_ids:
                        continue
                    p = pb_payment_map.get(pid)
                    if not p:
                        continue
                    eklenen_odeme_ids.add(pid)
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
                        "id": pid, "tarih": ptarih, "tur": "supplier_payment",
                        "aciklama": pattr.get("notes") or pattr.get("description") or "Tedarikçi ödemesi",
                        # Tedarikçiye ödeme bizim için para çıkışı (+)
                        "tutar": ptutar, "durum": "paid", "kalan": 0,
                    })
            if len(alis_faturalar) < 25:
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
        tip: str = "customer",
) -> dict:
    """
    Yeni cari oluşturur.
    tip: 'customer' (müşteri), 'supplier' (tedarikçi), 'customer_supplier' (her ikisi)
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    # Güvenli varsayılan — geçersiz değer gelirse customer'a düş
    tip_temiz = tip if tip in ("customer", "supplier", "customer_supplier") else "customer"
    try:
        attributes = {"name": firma_adi, "contact_type": "company", "account_type": tip_temiz}
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

def cari_guncelle(
        cari_id: str,
        firma_adi: str = None,
        email: str = None,
        telefon: str = None,
        vergi_no: str = None,
        vergi_dairesi: str = None,
        adres: str = None,
        tip: str = None,
        tenant_id=None,
) -> dict:
    """
    Mevcut cariyi günceller (PUT /contacts/{id}).
    Sadece dolu gelen alanlar gönderilir; diğerleri korunur.
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        attrs = {}
        if firma_adi:     attrs["name"]         = firma_adi
        if email:         attrs["email"]        = email
        if telefon:       attrs["phone"]        = telefon
        if vergi_no:      attrs["tax_number"]   = vergi_no
        if vergi_dairesi: attrs["tax_office"]   = vergi_dairesi
        if adres:         attrs["address"]      = adres
        if tip and tip in ("customer", "supplier", "customer_supplier"):
            attrs["account_type"] = tip
        if not attrs:
            return {"basarili": False, "hata": "Güncellenecek alan yok"}
        istek = {"data": {"id": str(cari_id), "type": "contacts", "attributes": attrs}}
        sonuc = parasut_put(f"/{cid}/contacts/{cari_id}", istek, tenant_id=tid)
        logger.info(f"Cari güncellendi: {cari_id} alanlar={list(attrs.keys())} (tenant={tid})")
        try:
            cari_cache_yenile(tenant_id=tid)
        except Exception:
            pass
        return {"basarili": True, "id": cari_id, "veri": sonuc.get("data", {}).get("attributes", {})}
    except Exception as e:
        logger.error(f"Cari güncelleme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


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
        istek = {"data": {"type": "payments", "attributes": {
            "description": aciklama or "Tahsilat", "date": bugun,
            "amount": tutar, "account_id": int(hesap_id),
        }}}
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


def tahsilat_sil(odeme_id: str, tenant_id=None, transaction_id: str = None) -> dict:
    """
    Paraşüt'te bir tahsilatı/ödemeyi siler.

    Paraşüt dokümantasyonuna göre tahsilatı silmek = tahsilatı oluşturan
    PARA AKIŞ İŞLEMİNİ (transaction) silmek demektir. Yani DELETE
    /transactions/{transaction_id} kullanılır — payment id DEĞİL.

    transaction_id verilirse direkt onunla siler.
    Verilmezse: payment id'sinden transaction id'sini bulmak gerekir
    (caller tarafından sağlanmalı). Geriye uyumluluk için son çare olarak
    payment id ile silmeyi de dener.
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        if transaction_id:
            parasut_delete(f"/{cid}/transactions/{transaction_id}", tenant_id=tid)
            logger.info(f"Tahsilat silindi (transaction_id={transaction_id}) (tenant={tid})")
        else:
            # Geriye uyumluluk: önce transactions, sonra payments dene
            try:
                parasut_delete(f"/{cid}/transactions/{odeme_id}", tenant_id=tid)
                logger.info(f"Tahsilat silindi (legacy, id={odeme_id} → transactions) (tenant={tid})")
            except Exception as e:
                logger.warning(f"/transactions/{odeme_id} silinemedi, /payments deneniyor: {e}")
                parasut_delete(f"/{cid}/payments/{odeme_id}", tenant_id=tid)
                logger.info(f"Tahsilat silindi (legacy, id={odeme_id} → payments) (tenant={tid})")
        try:
            cari_cache_yenile(tenant_id=tid)
        except Exception:
            pass
        return {"basarili": True, "id": odeme_id, "transaction_id": transaction_id}
    except Exception as e:
        logger.error(f"Tahsilat silme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


        return {"basarili": False, "hata": str(e)}


def cari_bazli_tahsilat(
        cari_id: str,
        tutar: float,
        aciklama: str = None,
        tarih: str = None,
        kasa_id: str = None,
        tenant_id=None,
) -> dict:
    """
    Fatura olmadan doğrudan müşteri cari hesabına TAHSİLAT ekler.

    Paraşüt V4 dokümanına göre:
      POST /contacts/{id}/contact_debit_transactions
      Attribute: ContactCollectionFormAttributes
      Açıklama: 'Tahsilat'
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        from datetime import datetime
        bugun    = tarih or datetime.now().strftime("%Y-%m-%d")
        hesap_id = kasa_id or _kasa_hesabi_bul(tenant_id=tid)
        if not hesap_id:
            return {"basarili": False, "hata": "Kasa/banka hesabı bulunamadı."}
        istek = {"data": {"type": "transactions", "attributes": {
            "description": aciklama or "Cari hesap tahsilatı",
            "date":        bugun,
            "amount":      tutar,
            "account_id":  int(hesap_id),
        }}}
        try:
            sonuc = parasut_post(
                f"/{cid}/contacts/{cari_id}/contact_debit_transactions",
                istek, tenant_id=tid,
            )
        except Exception:
            istek["data"]["attributes"]["amount"] = str(tutar)
            sonuc = parasut_post(
                f"/{cid}/contacts/{cari_id}/contact_debit_transactions",
                istek, tenant_id=tid,
            )
        tx_id = sonuc.get("data", {}).get("id")
        logger.info(f"Cari bazlı tahsilat (doc:debit): cari_id={cari_id} tutar={tutar:,.0f} TL tx={tx_id} (tenant={tid})")
        try:
            cari_cache_yenile(tenant_id=tid)
        except Exception:
            pass
        return {"basarili": True, "id": tx_id, "cari_id": cari_id, "tutar": tutar, "tarih": bugun}
    except Exception as e:
        logger.error(f"Cari bazlı tahsilat hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def cari_bazli_odeme(
        cari_id: str,
        tutar: float,
        aciklama: str = None,
        tarih: str = None,
        kasa_id: str = None,
        tenant_id=None,
) -> dict:
    """
    Fatura olmadan doğrudan tedarikçi cari hesabına ÖDEME ekler.

    Paraşüt V4 dokümanına göre:
      POST /contacts/{id}/contact_credit_transactions
      Attribute: ContactPaymentFormAttributes
      Açıklama: 'Ödeme'
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        from datetime import datetime
        bugun    = tarih or datetime.now().strftime("%Y-%m-%d")
        hesap_id = kasa_id or _kasa_hesabi_bul(tenant_id=tid)
        if not hesap_id:
            return {"basarili": False, "hata": "Kasa/banka hesabı bulunamadı."}
        istek = {"data": {"type": "transactions", "attributes": {
            "description": aciklama or "Cari hesap ödemesi",
            "date":        bugun,
            "amount":      tutar,
            "account_id":  int(hesap_id),
        }}}
        try:
            sonuc = parasut_post(
                f"/{cid}/contacts/{cari_id}/contact_credit_transactions",
                istek, tenant_id=tid,
            )
        except Exception:
            istek["data"]["attributes"]["amount"] = str(tutar)
            sonuc = parasut_post(
                f"/{cid}/contacts/{cari_id}/contact_credit_transactions",
                istek, tenant_id=tid,
            )
        tx_id = sonuc.get("data", {}).get("id")
        logger.info(f"Cari bazlı ödeme (doc:credit): cari_id={cari_id} tutar={tutar:,.0f} TL tx={tx_id} (tenant={tid})")
        try:
            cari_cache_yenile(tenant_id=tid)
        except Exception:
            pass
        return {"basarili": True, "id": tx_id, "cari_id": cari_id, "tutar": tutar, "tarih": bugun}
    except Exception as e:
        logger.error(f"Cari bazlı ödeme hatası: {e}")
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

        # Açık faturaları topla (overdue + not_due + unscheduled)
        faturalar = []
        for durum in ("overdue", "not_due", "unscheduled"):
            try:
                sonuc = parasut_get(
                    f"/{cid}/sales_invoices",
                    params={"filter[contact_id]": cari_id, "filter[payment_status]": durum,
                            "page[size]": 25, "sort": "issue_date"},
                    tenant_id=tid,
                )
                for f in sonuc.get("data", []):
                    attr  = f.get("attributes", {})
                    kalan = float(attr.get("remaining") or attr.get("gross_total") or 0)
                    if kalan > 0:
                        faturalar.append({"id": f["id"], "tarih": attr.get("issue_date", "-"),
                                           "tutar": float(attr.get("gross_total", 0)), "kalan": kalan})
            except Exception as fetch_err:
                logger.warning(f"Açık fatura {durum} sorgusu atlandı: {fetch_err}")

        # ── FALLBACK: Açık fatura yoksa cari hesabına direkt tahsilat ──
        # Paraşüt: contact_debit_transactions ile fatura bağı olmadan tahsilat
        if not faturalar:
            logger.info(f"{cari_adi} için açık fatura yok → cari bazlı tahsilat'a düşülüyor")
            cari_sonuc = cari_bazli_tahsilat(
                cari_id=cari_id, tutar=float(tutar),
                aciklama=aciklama or f"{cari_adi} cari tahsilatı",
                tarih=tarih, tenant_id=tid,
            )
            if cari_sonuc.get("basarili"):
                return {
                    "basarili": True, "cari": cari_adi, "tutar": float(tutar),
                    "tarih": tarih or "", "odenen_faturalar": [],
                    "cari_hesap_id": cari_sonuc.get("id"),
                    "kalan_odenmedi": 0, "basarisiz_fatura": 0,
                    "yontem": "cari_hesap",
                }
            return cari_sonuc

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
                "include": "stock_movements,contact",
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
                # Faturaya bağlı irsaliyeleri atla — invoices ilişkisi doluysa faturalanmış
                # Faturaya bağlı irsaliyeleri atla (Satış veya Alış faturası kesilmiş mi?)
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
                "currency":     attr.get("currency", "TRY"),
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
        istek = {"data": {"type": "payments", "attributes": {
            "description": notlar or "Hurda alım ödemesi", "date": bugun,
            "amount": tutar, "account_id": int(hesap_id),
        }}}
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
    """
    Stok giriş/çıkış hareketlerini döner. urun_id ile filtrelenebilir.
    include=product,contact — ürün adı + cari adı doğrudan Paraşüt'ten gelir;
    böylece main.py'de DB'ye join atmaya gerek kalmaz (firma değeri 'firma'
    alanına yazılır, fallback olarak shipment_document üzerinden DB'den de
    çekilebilir).
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        params = {"page[size]": min(limit, 25), "sort": "-date", "include": "product,contact"}
        if urun_id:
            params["filter[product_id]"] = urun_id
        try:
            sonuc = parasut_get(f"/{cid}/stock_movements", params=params, tenant_id=tid)
        except Exception as e:
            # include=contact desteklenmezse fallback (eski davranış)
            logger.warning(f"stock_movements include=contact başarısız, product'a düşülüyor: {e}")
            params["include"] = "product"
            sonuc = parasut_get(f"/{cid}/stock_movements", params=params, tenant_id=tid)
        included = sonuc.get("included", [])
        # Ürün adlarını included'dan al
        urun_map = {
            inc["id"]: inc.get("attributes", {}).get("name", "-")
            for inc in included
            if inc.get("type") == "products"
        }
        # Cari adlarını included'dan al
        contact_map = {
            inc["id"]: inc.get("attributes", {}).get("name", "-")
            for inc in included
            if inc.get("type") == "contacts"
        }
        hareketler = []
        for h in sonuc.get("data", []):
            attr = h.get("attributes", {})
            rels = h.get("relationships", {})
            # Ürün adını ilişkiden bul
            urun_rel  = rels.get("product", {}).get("data") or {}
            urun_id_r = urun_rel.get("id")
            urun_adi  = urun_map.get(urun_id_r) if urun_id_r else None
            # Cari adını ilişkiden bul (varsa)
            contact_rel = rels.get("contact", {}).get("data") or {}
            contact_id  = contact_rel.get("id") if isinstance(contact_rel, dict) else None
            firma_adi   = contact_map.get(contact_id) if contact_id else ""
            aciklama  = attr.get("description") or "-"   # "{plaka} — {malzeme} — {kg} kg"
            # Shipment document ID → fallback olarak DB'den firma çekmek için
            sd_rel    = rels.get("shipment_document", {}).get("data") or {}
            sd_id     = sd_rel.get("id")
            hareketler.append({
                "id":          h["id"],
                "tarih":       (attr.get("date") or attr.get("created_at") or "-")[:10],
                "miktar":      float(attr.get("quantity", 0) or 0),
                "firma":       firma_adi or "",
                "urun":        urun_adi or "-",
                "aciklama":    aciklama,
                "giris_mi":    attr.get("inflow", False),
                "irsaliye_id": sd_id,
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

        attrs = {"date": bugun, "amount": tutar, "description": not_}
        # Kaynak hesaptan çıkış (debit = para çıkışı)
        parasut_post(f"/{cid}/accounts/{kaynak_id}/debit_transactions",
                     {"data": {"type": "transactions", "attributes": attrs}}, tenant_id=tid)
        # Hedef hesaba giriş (credit = para girişi)
        parasut_post(f"/{cid}/accounts/{hedef_id}/credit_transactions",
                     {"data": {"type": "transactions", "attributes": attrs}}, tenant_id=tid)

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
            # customer_supplier / both → tedarikçi akışı (kantar fişi = gelen mal = alış)
            if t in ("supplier", "both", "customer_supplier"):
                return "supplier"
            return "customer"
    return "customer"


# ── SATIŞ FATURASI: İPTAL / ARŞİV / GÜNCELLEME ───────────────────────

def satis_faturasi_iptal(fatura_id: str, tenant_id=None) -> dict:
    """
    Satış faturasını iptal eder (cancel).
    Ödenmiş faturalar iptal edilemez — önce tahsilat silinmeli.
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        parasut_delete(f"/{cid}/sales_invoices/{fatura_id}/cancel", tenant_id=tid)
        logger.info(f"Satış faturası iptal edildi: {fatura_id} (tenant={tid})")
        return {"basarili": True, "fatura_id": fatura_id, "durum": "iptal"}
    except Exception as e:
        logger.error(f"Satış faturası iptal hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def satis_faturasi_geri_al(fatura_id: str, tenant_id=None) -> dict:
    """İptal edilmiş satış faturasını geri yükler."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        parasut_patch(f"/{cid}/sales_invoices/{fatura_id}/recover", tenant_id=tid)
        logger.info(f"Satış faturası geri alındı: {fatura_id} (tenant={tid})")
        return {"basarili": True, "fatura_id": fatura_id, "durum": "geri_alindi"}
    except Exception as e:
        logger.error(f"Satış faturası geri alma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def satis_faturasi_arsivle(fatura_id: str, tenant_id=None) -> dict:
    """Satış faturasını arşive taşır."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        parasut_patch(f"/{cid}/sales_invoices/{fatura_id}/archive", tenant_id=tid)
        logger.info(f"Satış faturası arşivlendi: {fatura_id} (tenant={tid})")
        return {"basarili": True, "fatura_id": fatura_id, "durum": "arsivlendi"}
    except Exception as e:
        logger.error(f"Satış faturası arşivleme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def satis_faturasi_arsivden_cikar(fatura_id: str, tenant_id=None) -> dict:
    """
    Arşivdeki satış faturasını aktife alır.
    Paraşüt V4 dokümantasyonu 'unarchive' action'ı kullanıyor.
    Eski API 'recover_from_archive' ile çalışıyordu — geriye uyumluluk için
    önce yenisini, başarısızsa eskisini deniyoruz.
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        try:
            parasut_patch(f"/{cid}/sales_invoices/{fatura_id}/unarchive", tenant_id=tid)
        except Exception as e:
            logger.warning(f"unarchive başarısız, recover_from_archive deneniyor: {e}")
            parasut_patch(f"/{cid}/sales_invoices/{fatura_id}/recover_from_archive", tenant_id=tid)
        logger.info(f"Satış faturası arşivden çıkarıldı: {fatura_id} (tenant={tid})")
        return {"basarili": True, "fatura_id": fatura_id, "durum": "aktif"}
    except Exception as e:
        logger.error(f"Satış faturası arşivden çıkarma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


# Paraşüt sales_invoices PUT için kabul edilen attribute alanları
_SALES_INVOICE_ATTRIBUTES = {
    "description", "issue_date", "due_date", "currency",
    "invoice_no", "invoice_series",
    "exchange_rate", "withholding_rate", "vat_withholding_code",
    "invoice_discount_type", "invoice_discount", "invoice_discount_amount",
    "billing_address", "billing_phone", "billing_fax",
    "tax_office", "tax_number", "city", "district", "country",
    "order_no", "order_date",
    "shipment_addres", "shipment_included",
    "cash_sale", "is_abroad", "payer_tax_number",
}

# Paraşüt purchase_bills PUT için kabul edilen attribute alanları
_PURCHASE_BILL_ATTRIBUTES = {
    "description", "issue_date", "due_date", "currency",
    "invoice_no", "invoice_series",
    "exchange_rate", "withholding_rate", "vat_withholding_code",
    "invoice_discount_type", "invoice_discount", "invoice_discount_amount",
    "order_no", "order_date",
    "billing_address", "billing_phone", "billing_fax",
    "tax_office", "tax_number", "city", "district", "country",
    "cash_sale", "payer_tax_number",
}


def _attrs_ve_rels_hazirla(alanlar: dict, attr_whitelist: set) -> tuple[dict, dict]:
    """
    Generic helper: gelen `alanlar` dict'inden attribute ve relationship'leri ayırır.
    İlişki anahtarları: contact_id, category_id, tag_ids, supplier_id.
    """
    attrs = {}
    for k, v in (alanlar or {}).items():
        if v is None or v == "":
            continue
        if k in attr_whitelist:
            attrs[k] = v
    rels = {}
    if alanlar.get("contact_id"):
        rels["contact"] = {"data": {"type": "contacts", "id": str(alanlar["contact_id"])}}
    if alanlar.get("supplier_id"):
        rels["supplier"] = {"data": {"type": "contacts", "id": str(alanlar["supplier_id"])}}
    if alanlar.get("category_id"):
        rels["category"] = {"data": {"type": "item_categories", "id": str(alanlar["category_id"])}}
    tag_ids = alanlar.get("tag_ids")
    if isinstance(tag_ids, (list, tuple)) and tag_ids:
        rels["tags"] = {"data": [{"type": "tags", "id": str(tid)} for tid in tag_ids]}
    return attrs, rels


def satis_faturasi_guncelle(
        fatura_id: str,
        aciklama: str = None,
        issue_date: str = None,
        due_date: str = None,
        currency: str = None,
        tenant_id=None,
        **diger_alanlar,
) -> dict:
    """
    Satış faturası üst bilgilerini günceller. Paraşüt V4 sales_invoices PUT
    endpoint'inin kabul ettiği TÜM alanları destekler.
    Yaygın alanlar parametre olarak alınır; diğerleri **diger_alanlar ile.

    İlişkiler (geçilirse relationships'e dönüşür):
      - contact_id  → müşteriyi değiştirir
      - category_id → kategori atar
      - tag_ids     → liste, etiketler atanır
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        alanlar = dict(diger_alanlar)
        if aciklama   is not None: alanlar["description"] = aciklama
        if issue_date is not None: alanlar["issue_date"]  = issue_date
        if due_date   is not None: alanlar["due_date"]    = due_date
        if currency   is not None: alanlar["currency"]    = currency

        attrs, rels = _attrs_ve_rels_hazirla(alanlar, _SALES_INVOICE_ATTRIBUTES)
        if not attrs and not rels:
            return {"basarili": False, "hata": "Güncellenecek alan belirtilmedi"}

        data = {"id": str(fatura_id), "type": "sales_invoices", "attributes": attrs}
        if rels:
            data["relationships"] = rels
        sonuc = parasut_put(f"/{cid}/sales_invoices/{fatura_id}", {"data": data}, tenant_id=tid)
        logger.info(
            f"Satış faturası güncellendi: {fatura_id} "
            f"attrs={list(attrs.keys())} rels={list(rels.keys())} (tenant={tid})"
        )
        return {"basarili": True, "fatura_id": fatura_id, "veri": sonuc.get("data", {}).get("attributes", {})}
    except Exception as e:
        logger.error(f"Satış faturası güncelleme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


# ── ALIŞ FATURASI: OLUŞTURMA / İPTAL / ARŞİV / GÜNCELLEME ────────────

def alis_faturasi_olustur(
        cari_adi: str,
        kalemler: list,
        aciklama: str = None,
        issue_date: str = None,
        due_date: str = None,
        currency: str = "TRL",
        item_type: str = "purchase_bill",
        tenant_id=None,
) -> dict:
    """
    Detaylı alış faturası (purchase_bill) oluşturur.
    kalemler: [{aciklama, miktar, birim_fiyat, vat_rate?, product_id?}, ...]
    item_type: 'purchase_bill' (varsayılan), 'expense', 'refund'

    NOT: Paraşüt purchase_bills için currency 'TRL' bekler (TRY değil).
         Her kalemde 'product' ilişkisi zorunludur — verilmezse hurda_product_id otomatik kullanılır.
    """
    tid     = _resolve_tid(tenant_id)
    creds   = _tenant_credentials(tid)
    cid     = creds["company_id"]
    hurda_p = creds.get("hurda_product_id") or PARASUT_HURDA_PRODUCT_ID
    try:
        from datetime import datetime
        bugun   = datetime.now().strftime("%Y-%m-%d")
        cari_id = cari_id_bul(cari_adi, tenant_id=tid)
        if not cari_id:
            return {"basarili": False, "hata": f"'{cari_adi}' adlı cari bulunamadı."}

        detaylar = []
        toplam   = 0.0
        for k in (kalemler or []):
            miktar = float(k.get("miktar") or 0)
            bf     = float(k.get("birim_fiyat") or 0)
            if miktar <= 0 or bf <= 0:
                continue
            toplam += miktar * bf
            # Paraşüt purchase_bill_details için 'product' zorunlu
            urun_id = k.get("product_id") or hurda_p
            if not urun_id:
                return {"basarili": False, "hata": "product_id verilmedi ve hurda_product_id yapılandırılmamış"}
            d = {
                "type": "purchase_bill_details",
                "attributes": {
                    "quantity":    miktar,
                    "unit_price":  str(bf),
                    "vat_rate":    k.get("vat_rate", 0),
                    "description": k.get("aciklama") or "-",
                },
                "relationships": {
                    "product": {"data": {"type": "products", "id": str(urun_id)}}
                },
            }
            detaylar.append(d)

        if not detaylar:
            return {"basarili": False, "hata": "Geçerli kalem yok"}

        istek = {
            "data": {
                "type": "purchase_bills",
                "attributes": {
                    "item_type":   item_type,
                    "description": aciklama or f"{cari_adi} alış",
                    "issue_date":  issue_date or bugun,
                    "due_date":    due_date or bugun,
                    "currency":    currency,
                },
                "relationships": {
                    "supplier": {"data": {"type": "contacts", "id": str(cari_id)}},
                    "details":  {"data": detaylar},
                },
            }
        }
        sonuc     = parasut_post(f"/{cid}/purchase_bills", istek, tenant_id=tid)
        fatura_id = sonuc["data"]["id"]
        logger.info(f"Alış faturası oluşturuldu: {fatura_id} cari={cari_adi} tutar={toplam:,.2f} (tenant={tid})")
        return {
            "basarili":  True,
            "fatura_id": fatura_id,
            "tutar":     toplam,
            "cari":      cari_adi,
            "cari_id":   cari_id,
        }
    except Exception as e:
        logger.error(f"Alış faturası oluşturma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def alis_faturasi_basit_olustur(
        cari_adi: str,
        tutar: float,
        aciklama: str = None,
        issue_date: str = None,
        due_date: str = None,
        currency: str = "TRL",
        vat_rate: float = 0,
        tenant_id=None,
) -> dict:
    """
    Tek tutarlı basit alış faturası — kalem detayı olmadan.
    Hurda iş akışında en yaygın kullanım.

    NOT: Paraşüt purchase_bills için currency 'TRL' bekler.
    Otomatik olarak hurda_product_id kullanılır (Paraşüt 'product' alanını zorunlu kılar).
    """
    kalemler = [{
        "aciklama":    aciklama or "Hurda alım",
        "miktar":      1,
        "birim_fiyat": float(tutar),
        "vat_rate":    vat_rate,
    }]
    return alis_faturasi_olustur(
        cari_adi=cari_adi,
        kalemler=kalemler,
        aciklama=aciklama,
        issue_date=issue_date,
        due_date=due_date,
        currency=currency,
        tenant_id=tenant_id,
    )


def alis_faturasi_iptal(fatura_id: str, tenant_id=None) -> dict:
    """Alış faturasını iptal eder."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        parasut_delete(f"/{cid}/purchase_bills/{fatura_id}/cancel", tenant_id=tid)
        logger.info(f"Alış faturası iptal edildi: {fatura_id} (tenant={tid})")
        return {"basarili": True, "fatura_id": fatura_id, "durum": "iptal"}
    except Exception as e:
        logger.error(f"Alış faturası iptal hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def alis_faturasi_geri_al(fatura_id: str, tenant_id=None) -> dict:
    """İptal edilmiş alış faturasını geri yükler."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        parasut_patch(f"/{cid}/purchase_bills/{fatura_id}/recover", tenant_id=tid)
        logger.info(f"Alış faturası geri alındı: {fatura_id} (tenant={tid})")
        return {"basarili": True, "fatura_id": fatura_id, "durum": "geri_alindi"}
    except Exception as e:
        logger.error(f"Alış faturası geri alma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def alis_faturasi_arsivle(fatura_id: str, tenant_id=None) -> dict:
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        parasut_patch(f"/{cid}/purchase_bills/{fatura_id}/archive", tenant_id=tid)
        logger.info(f"Alış faturası arşivlendi: {fatura_id} (tenant={tid})")
        return {"basarili": True, "fatura_id": fatura_id, "durum": "arsivlendi"}
    except Exception as e:
        logger.error(f"Alış faturası arşivleme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def alis_faturasi_arsivden_cikar(fatura_id: str, tenant_id=None) -> dict:
    """V4 'unarchive' önce, geriye uyumlu 'recover_from_archive' fallback."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        try:
            parasut_patch(f"/{cid}/purchase_bills/{fatura_id}/unarchive", tenant_id=tid)
        except Exception as e:
            logger.warning(f"unarchive başarısız, recover_from_archive deneniyor: {e}")
            parasut_patch(f"/{cid}/purchase_bills/{fatura_id}/recover_from_archive", tenant_id=tid)
        logger.info(f"Alış faturası arşivden çıkarıldı: {fatura_id} (tenant={tid})")
        return {"basarili": True, "fatura_id": fatura_id, "durum": "aktif"}
    except Exception as e:
        logger.error(f"Alış faturası arşivden çıkarma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def alis_faturasi_guncelle(
        fatura_id: str,
        aciklama: str = None,
        issue_date: str = None,
        due_date: str = None,
        currency: str = None,
        tenant_id=None,
        **diger_alanlar,
) -> dict:
    """
    Alış faturası üst bilgilerini günceller. Paraşüt V4 purchase_bills PUT
    endpoint'inin kabul ettiği TÜM alanları destekler.

    İlişkiler:
      - supplier_id → tedarikçiyi değiştirir
      - category_id → kategori atar
      - tag_ids     → etiketler
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        alanlar = dict(diger_alanlar)
        if aciklama   is not None: alanlar["description"] = aciklama
        if issue_date is not None: alanlar["issue_date"]  = issue_date
        if due_date   is not None: alanlar["due_date"]    = due_date
        if currency   is not None: alanlar["currency"]    = currency

        # Frontend bazen contact_id gönderebilir; purchase_bill için bu
        # aslında supplier_id'dir — otomatik dönüşüm yap
        if "contact_id" in alanlar and "supplier_id" not in alanlar:
            alanlar["supplier_id"] = alanlar.pop("contact_id")

        attrs, rels = _attrs_ve_rels_hazirla(alanlar, _PURCHASE_BILL_ATTRIBUTES)
        if not attrs and not rels:
            return {"basarili": False, "hata": "Güncellenecek alan belirtilmedi"}

        data = {"id": str(fatura_id), "type": "purchase_bills", "attributes": attrs}
        if rels:
            data["relationships"] = rels
        sonuc = parasut_put(f"/{cid}/purchase_bills/{fatura_id}", {"data": data}, tenant_id=tid)
        logger.info(
            f"Alış faturası güncellendi: {fatura_id} "
            f"attrs={list(attrs.keys())} rels={list(rels.keys())} (tenant={tid})"
        )
        return {"basarili": True, "fatura_id": fatura_id, "veri": sonuc.get("data", {}).get("attributes", {})}
    except Exception as e:
        logger.error(f"Alış faturası güncelleme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def fatura_detay_universal(fatura_id: str, tip: str = "customer", tenant_id=None) -> dict:
    """
    Tip-aware fatura detay getirici.
    tip='customer' → satış faturası (sales_invoices show)
    tip='supplier' → alış faturası (purchase_bills show)
    """
    if tip == "supplier":
        return alis_faturasi_detay_getir(fatura_id, tenant_id=tenant_id)
    return fatura_detay_getir(fatura_id, tenant_id=tenant_id)


def fatura_iptal_universal(fatura_id: str, tip: str = "customer", tenant_id=None) -> dict:
    """Tip-aware fatura iptal (cancel)."""
    if tip == "supplier":
        return alis_faturasi_iptal(fatura_id, tenant_id=tenant_id)
    return satis_faturasi_iptal(fatura_id, tenant_id=tenant_id)


def fatura_guncelle_universal(
        fatura_id: str,
        tip: str = "customer",
        aciklama: str = None,
        issue_date: str = None,
        due_date: str = None,
        currency: str = None,
        tenant_id=None,
        **diger_alanlar,
) -> dict:
    """Tip-aware fatura güncelle (PUT). Tüm Paraşüt alanlarını destekler."""
    if tip == "supplier":
        return alis_faturasi_guncelle(
            fatura_id, aciklama, issue_date, due_date, currency, tenant_id,
            **diger_alanlar,
        )
    return satis_faturasi_guncelle(
        fatura_id, aciklama, issue_date, due_date, currency, tenant_id,
        **diger_alanlar,
    )


def alis_faturasi_detay_getir(fatura_id: str, tenant_id=None) -> dict:
    """Alış faturası detayını getirir (kalemler + ödemeler dahil)."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc    = parasut_get(
            f"/{cid}/purchase_bills/{fatura_id}",
            params={"include": "supplier,details,details.product,payments,payments.transaction"},
            tenant_id=tid,
        )
        data     = sonuc.get("data", {})
        attr     = data.get("attributes", {})
        included = sonuc.get("included", [])
        rels     = data.get("relationships", {})

        detail_ids = {d["id"] for d in rels.get("details", {}).get("data", [])}
        kalemler = []
        for inc in included:
            if inc.get("type") == "purchase_bill_details" and inc["id"] in detail_ids:
                a = inc.get("attributes", {})
                try:
                    miktar      = float(a.get("quantity") or 0)
                    birim_fiyat = float(a.get("unit_price") or 0)
                except (ValueError, TypeError):
                    miktar = birim_fiyat = 0.0
                kalemler.append({
                    "aciklama":    a.get("description") or "-",
                    "miktar":      miktar,
                    "birim_fiyat": birim_fiyat,
                    "tutar":       miktar * birim_fiyat,
                })

        payment_ids = {p["id"] for p in rels.get("payments", {}).get("data", [])}
        odemeler = []
        for inc in included:
            if inc.get("type") == "payments" and inc["id"] in payment_ids:
                a = inc.get("attributes", {})
                tx_rel = inc.get("relationships", {}).get("transaction", {}).get("data") or {}
                tx_id  = tx_rel.get("id") if isinstance(tx_rel, dict) else None
                odemeler.append({
                    "id":             inc["id"],
                    "transaction_id": tx_id,
                    "tarih":          a.get("date", "-"),
                    "tutar":          float(a.get("amount", 0) or 0),
                    "aciklama":       a.get("description", "-"),
                })

        try:
            tutar = float(attr.get("gross_total") or 0)
            kalan = float(attr.get("remaining") or 0)
        except (ValueError, TypeError):
            tutar = kalan = 0.0

        return {
            "id":       fatura_id,
            "tarih":    attr.get("issue_date", "-"),
            "vade":     attr.get("due_date", "-"),
            "tutar":    tutar,
            "kalan":    kalan,
            "durum":    attr.get("payment_status", "-"),
            "aciklama": attr.get("description", "-"),
            "kalemler": kalemler,
            "odemeler": odemeler,
        }
    except Exception as e:
        logger.error(f"Alış faturası detay hatası: {e}")
        return {}


# ── E-ARŞİV / E-FATURA RESMİLEŞTİRME ──────────────────────────────────

def e_fatura_mukellef_kontrol(vkn: str, tenant_id=None) -> dict:
    """
    VKN'nin e-fatura mükellefi olup olmadığını sorgular.
    Mükellefse e_invoice_address (etiket) döner — bu etiket e_fatura_olustur'a verilir.
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(
            f"/{cid}/e_invoice_inboxes",
            params={"filter[vkn]": vkn},
            tenant_id=tid,
        )
        data = sonuc.get("data", [])
        if data:
            attr = data[0].get("attributes", {})
            etiket = (
                attr.get("e_invoice_address")
                or attr.get("address")
                or attr.get("regulation_number")
            )
            logger.info(f"E-Fatura mükellefi: vkn={vkn} etiket={etiket}")
            return {
                "mukellef": True,
                "vkn":      vkn,
                "etiket":   etiket,
                "unvan":    attr.get("name"),
                "veri":     attr,
            }
        logger.info(f"E-Fatura mükellefi DEĞİL: vkn={vkn} — e_arşiv kesilmeli")
        return {"mukellef": False, "vkn": vkn}
    except Exception as e:
        logger.error(f"E-Fatura mükellef kontrol hatası: {e}")
        return {"mukellef": False, "vkn": vkn, "hata": str(e)}


def trackable_job_durum(job_id: str, tenant_id=None) -> dict:
    """
    Asenkron işlemin (e-fatura/e-arşiv) durumunu sorgular.
    status: pending / started / done / failed
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(f"/{cid}/trackable_jobs/{job_id}", tenant_id=tid)
        attr  = sonuc.get("data", {}).get("attributes", {})
        return {
            "id":     job_id,
            "durum":  attr.get("status"),
            "hatalar": attr.get("errors") or [],
            "veri":   attr,
        }
    except Exception as e:
        logger.error(f"Trackable job sorgu hatası: {e}")
        return {"id": job_id, "durum": "unknown", "hata": str(e)}


def trackable_job_bekle(job_id: str, max_bekleme_sn: int = 30, tenant_id=None) -> dict:
    """
    Trackable job tamamlanana kadar polling yapar.
    done / failed döndüğünde veya max_bekleme aşıldığında çıkar.
    """
    baslangic = time.time()
    son_durum = {"id": job_id, "durum": "pending"}
    while time.time() - baslangic < max_bekleme_sn:
        son_durum = trackable_job_durum(job_id, tenant_id=tenant_id)
        st = son_durum.get("durum")
        if st in ("done", "failed"):
            return son_durum
        time.sleep(1.5)
    son_durum["durum"] = son_durum.get("durum") or "timeout"
    logger.warning(f"Trackable job zaman aşımı: {job_id} (son durum: {son_durum.get('durum')})")
    return son_durum


def e_arsiv_olustur(
        fatura_id: str,
        vat_withholding_code: str = None,
        internet_sale: dict = None,
        shipment: dict = None,
        bekle: bool = True,
        tenant_id=None,
) -> dict:
    """
    Satış faturasını E-ARŞİV olarak resmileştirir (alıcı e-fatura mükellefi DEĞİLse).
    fatura_id önce normal sales_invoice olarak oluşturulmuş olmalı.

    internet_sale: opsiyonel, e-ticaret satışları için
      {"url": "...", "payment_type": "kredikarti/eft/...", "payment_platform": "...", "payment_date": "YYYY-MM-DD"}
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        attrs = {}
        if vat_withholding_code: attrs["vat_withholding_code"] = vat_withholding_code
        if internet_sale:        attrs["internet_sale"] = internet_sale
        if shipment:             attrs["shipment"] = shipment

        istek = {
            "data": {
                "type": "e_archives",
                "attributes": attrs,
                "relationships": {
                    "sales_invoice": {"data": {"type": "sales_invoices", "id": str(fatura_id)}}
                }
            }
        }
        sonuc  = parasut_post(f"/{cid}/e_archives", istek, tenant_id=tid)
        job_id = sonuc.get("data", {}).get("id")
        logger.info(f"E-Arşiv oluşturma başlatıldı: fatura={fatura_id} job={job_id} (tenant={tid})")

        if bekle and job_id:
            durum = trackable_job_bekle(job_id, max_bekleme_sn=30, tenant_id=tid)
            basarili = durum.get("durum") == "done"
            return {
                "basarili":  basarili,
                "fatura_id": fatura_id,
                "job_id":    job_id,
                "durum":     durum.get("durum"),
                "hatalar":   durum.get("hatalar", []),
            }
        return {"basarili": True, "fatura_id": fatura_id, "job_id": job_id, "durum": "pending"}
    except Exception as e:
        logger.error(f"E-Arşiv oluşturma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def e_fatura_olustur(
        fatura_id: str,
        to_address: str,
        scenario: str = "basic",
        vat_withholding_code: str = None,
        note: str = None,
        bekle: bool = True,
        tenant_id=None,
) -> dict:
    """
    Satış faturasını resmi E-FATURA olarak gönderir (alıcı e-fatura mükellefiyse).
    to_address: e_fatura_mukellef_kontrol'den dönen etiket (PK numarası).
    scenario  : 'basic' (varsayılan) veya 'commercial' (ticari)
    """
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        attrs = {"scenario": scenario, "to": to_address}
        if vat_withholding_code: attrs["vat_withholding_code"] = vat_withholding_code
        if note:                 attrs["note"] = note

        istek = {
            "data": {
                "type": "e_invoices",
                "attributes": attrs,
                "relationships": {
                    "sales_invoice": {"data": {"type": "sales_invoices", "id": str(fatura_id)}}
                }
            }
        }
        sonuc  = parasut_post(f"/{cid}/e_invoices", istek, tenant_id=tid)
        job_id = sonuc.get("data", {}).get("id")
        logger.info(f"E-Fatura gönderme başlatıldı: fatura={fatura_id} to={to_address} job={job_id} (tenant={tid})")

        if bekle and job_id:
            durum = trackable_job_bekle(job_id, max_bekleme_sn=30, tenant_id=tid)
            basarili = durum.get("durum") == "done"
            return {
                "basarili":  basarili,
                "fatura_id": fatura_id,
                "job_id":    job_id,
                "durum":     durum.get("durum"),
                "hatalar":   durum.get("hatalar", []),
            }
        return {"basarili": True, "fatura_id": fatura_id, "job_id": job_id, "durum": "pending"}
    except Exception as e:
        logger.error(f"E-Fatura oluşturma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def e_arsiv_detay(e_arsiv_id: str, tenant_id=None) -> dict:
    """Oluşturulmuş e-arşivin detayını döner (UUID, durum, vb.)."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(f"/{cid}/e_archives/{e_arsiv_id}", tenant_id=tid)
        attr  = sonuc.get("data", {}).get("attributes", {})
        return {"id": e_arsiv_id, "veri": attr}
    except Exception as e:
        logger.error(f"E-Arşiv detay hatası: {e}")
        return {"hata": str(e)}


def e_fatura_detay(e_fatura_id: str, tenant_id=None) -> dict:
    """Oluşturulmuş e-faturanın detayını döner (UUID, ETTN, durum)."""
    tid   = _resolve_tid(tenant_id)
    creds = _tenant_credentials(tid)
    cid   = creds["company_id"]
    try:
        sonuc = parasut_get(f"/{cid}/e_invoices/{e_fatura_id}", tenant_id=tid)
        attr  = sonuc.get("data", {}).get("attributes", {})
        return {"id": e_fatura_id, "veri": attr}
    except Exception as e:
        logger.error(f"E-Fatura detay hatası: {e}")
        return {"hata": str(e)}


def fatura_resmilestir_otomatik(
        fatura_id: str,
        vkn: str = None,
        tckn: str = None,
        bekle: bool = True,
        tenant_id=None,
) -> dict:
    """
    Akıllı resmileştirme:
    - VKN verilmişse mükellef kontrolü yapılır → mükellefse e_fatura, değilse e_arşiv
    - Sadece TCKN/hiçbir şey verilmemişse → e_arşiv

    Dönüş: {basarili, fatura_id, job_id, tip: 'e_fatura'|'e_arsiv', durum, ...}
    """
    tid = _resolve_tid(tenant_id)
    try:
        if vkn:
            kontrol = e_fatura_mukellef_kontrol(vkn, tenant_id=tid)
            if kontrol.get("mukellef") and kontrol.get("etiket"):
                logger.info(f"VKN {vkn} e-fatura mükellefi → e_fatura kesiliyor")
                sonuc = e_fatura_olustur(
                    fatura_id, to_address=kontrol["etiket"],
                    bekle=bekle, tenant_id=tid,
                )
                sonuc["tip"] = "e_fatura"
                return sonuc
        logger.info(f"E-Arşiv kesiliyor: fatura={fatura_id} (vkn={vkn}, tckn={tckn})")
        sonuc = e_arsiv_olustur(fatura_id, bekle=bekle, tenant_id=tid)
        sonuc["tip"] = "e_arsiv"
        return sonuc
    except Exception as e:
        logger.error(f"Otomatik resmileştirme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


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


async def async_tahsilat_sil(odeme_id: str, tenant_id=None, transaction_id: str = None) -> dict:
    return await asyncio.to_thread(tahsilat_sil, odeme_id, tenant_id, transaction_id)


async def async_cari_bazli_tahsilat(
        cari_id: str, tutar: float, aciklama: str = None,
        tarih: str = None, kasa_id: str = None, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        cari_bazli_tahsilat, cari_id, tutar, aciklama, tarih, kasa_id, tenant_id
    )


async def async_cari_bazli_odeme(
        cari_id: str, tutar: float, aciklama: str = None,
        tarih: str = None, kasa_id: str = None, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        cari_bazli_odeme, cari_id, tutar, aciklama, tarih, kasa_id, tenant_id
    )


async def async_cari_guncelle(
        cari_id: str, firma_adi: str = None, email: str = None,
        telefon: str = None, vergi_no: str = None, vergi_dairesi: str = None,
        adres: str = None, tip: str = None, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        cari_guncelle, cari_id, firma_adi, email, telefon,
        vergi_no, vergi_dairesi, adres, tip, tenant_id,
    )


async def async_cari_faturalari_getir(
        cari_id: str, limit: int = 25, sayfa: int = 1,
        baslangic: str = None, bitis: str = None, durum: str = None,
        tip: str = "customer", tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        cari_faturalari_getir, cari_id, limit, sayfa, baslangic, bitis, durum, tip, tenant_id
    )


async def async_fatura_detay_getir(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(fatura_detay_getir, fatura_id, tenant_id)


async def async_fatura_detay_universal(fatura_id: str, tip: str = "customer", tenant_id=None) -> dict:
    return await asyncio.to_thread(fatura_detay_universal, fatura_id, tip, tenant_id)


async def async_fatura_iptal_universal(fatura_id: str, tip: str = "customer", tenant_id=None) -> dict:
    return await asyncio.to_thread(fatura_iptal_universal, fatura_id, tip, tenant_id)


async def async_fatura_guncelle_universal(
        fatura_id: str, tip: str = "customer",
        aciklama: str = None, issue_date: str = None,
        due_date: str = None, currency: str = None, tenant_id=None,
        **diger_alanlar,
) -> dict:
    import functools as _ft
    return await asyncio.to_thread(
        _ft.partial(
            fatura_guncelle_universal, fatura_id, tip, aciklama,
            issue_date, due_date, currency, tenant_id,
            **diger_alanlar,
        )
    )


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


async def async_parasut_put(endpoint: str, veri: dict, tenant_id=None) -> dict:
    return await asyncio.to_thread(parasut_put, endpoint, veri, tenant_id)


async def async_parasut_patch(endpoint: str, veri: dict = None, tenant_id=None) -> dict:
    return await asyncio.to_thread(parasut_patch, endpoint, veri, tenant_id)


# ── SATIŞ FATURASI ASYNC ──────────────────────────────────────────────

async def async_satis_faturasi_iptal(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(satis_faturasi_iptal, fatura_id, tenant_id)


async def async_satis_faturasi_geri_al(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(satis_faturasi_geri_al, fatura_id, tenant_id)


async def async_satis_faturasi_arsivle(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(satis_faturasi_arsivle, fatura_id, tenant_id)


async def async_satis_faturasi_arsivden_cikar(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(satis_faturasi_arsivden_cikar, fatura_id, tenant_id)


async def async_satis_faturasi_guncelle(
        fatura_id: str, aciklama: str = None, issue_date: str = None,
        due_date: str = None, currency: str = None, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        satis_faturasi_guncelle, fatura_id, aciklama, issue_date, due_date, currency, tenant_id
    )


# ── ALIŞ FATURASI ASYNC ───────────────────────────────────────────────

async def async_alis_faturasi_olustur(
        cari_adi: str, kalemler: list,
        aciklama: str = None, issue_date: str = None, due_date: str = None,
        currency: str = "TRL", item_type: str = "purchase_bill", tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        alis_faturasi_olustur, cari_adi, kalemler, aciklama,
        issue_date, due_date, currency, item_type, tenant_id,
    )


async def async_alis_faturasi_basit_olustur(
        cari_adi: str, tutar: float,
        aciklama: str = None, issue_date: str = None, due_date: str = None,
        currency: str = "TRL", vat_rate: float = 0, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        alis_faturasi_basit_olustur, cari_adi, tutar, aciklama,
        issue_date, due_date, currency, vat_rate, tenant_id,
    )


async def async_alis_faturasi_iptal(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(alis_faturasi_iptal, fatura_id, tenant_id)


async def async_alis_faturasi_geri_al(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(alis_faturasi_geri_al, fatura_id, tenant_id)


async def async_alis_faturasi_arsivle(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(alis_faturasi_arsivle, fatura_id, tenant_id)


async def async_alis_faturasi_arsivden_cikar(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(alis_faturasi_arsivden_cikar, fatura_id, tenant_id)


async def async_alis_faturasi_guncelle(
        fatura_id: str, aciklama: str = None, issue_date: str = None,
        due_date: str = None, currency: str = None, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        alis_faturasi_guncelle, fatura_id, aciklama, issue_date, due_date, currency, tenant_id
    )


async def async_alis_faturasi_detay_getir(fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(alis_faturasi_detay_getir, fatura_id, tenant_id)


# ── E-ARŞİV / E-FATURA ASYNC ──────────────────────────────────────────

async def async_e_fatura_mukellef_kontrol(vkn: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(e_fatura_mukellef_kontrol, vkn, tenant_id)


async def async_trackable_job_durum(job_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(trackable_job_durum, job_id, tenant_id)


async def async_trackable_job_bekle(job_id: str, max_bekleme_sn: int = 30, tenant_id=None) -> dict:
    return await asyncio.to_thread(trackable_job_bekle, job_id, max_bekleme_sn, tenant_id)


async def async_e_arsiv_olustur(
        fatura_id: str, vat_withholding_code: str = None,
        internet_sale: dict = None, shipment: dict = None,
        bekle: bool = True, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        e_arsiv_olustur, fatura_id, vat_withholding_code,
        internet_sale, shipment, bekle, tenant_id,
    )


async def async_e_fatura_olustur(
        fatura_id: str, to_address: str,
        scenario: str = "basic", vat_withholding_code: str = None,
        note: str = None, bekle: bool = True, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        e_fatura_olustur, fatura_id, to_address, scenario,
        vat_withholding_code, note, bekle, tenant_id,
    )


async def async_e_arsiv_detay(e_arsiv_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(e_arsiv_detay, e_arsiv_id, tenant_id)


async def async_e_fatura_detay(e_fatura_id: str, tenant_id=None) -> dict:
    return await asyncio.to_thread(e_fatura_detay, e_fatura_id, tenant_id)


async def async_fatura_resmilestir_otomatik(
        fatura_id: str, vkn: str = None, tckn: str = None,
        bekle: bool = True, tenant_id=None,
) -> dict:
    return await asyncio.to_thread(
        fatura_resmilestir_otomatik, fatura_id, vkn, tckn, bekle, tenant_id
    )
