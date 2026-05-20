# app/parasut/cari.py
# ---------------------------------------------------------------------
# CARI ISLEMLERI - listesi, cache, fuzzy id bulma, CRUD, tip tespiti
# ---------------------------------------------------------------------

import re
import time
import difflib

from app.core.logger import logger
from ._state import (
    _cari_caches, _cari_cache_times, _cache_locks,
    CACHE_SURE, _resolve_tid, _get_lock,
)
from .token import _tenant_credentials, get_tenant_config
from ._http import parasut_get, parasut_post, parasut_put


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


