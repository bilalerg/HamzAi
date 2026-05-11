# client.py — Paraşüt API İstemcisi
#
# ASYNC KULLANIM NOTU:
# Bu modüldeki tüm fonksiyonlar senkron (requests kütüphanesi).
# FastAPI async endpoint'lerinden çağırırken dosyanın sonundaki
# async_* wrapper'ları kullan:
#
#   cariler = await async_cari_listesi_getir()
#   sonuc   = await async_parasut_post(endpoint, veri)
#
# Bu wrapper'lar asyncio.to_thread() kullanır — event loop bloklanmaz.

import re
import difflib
import asyncio
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
_access_token     = None
_refresh_token    = None
_token_expires_at = 0

# ── CARİ CACHE ────────────────────────────────────────────────────────
_cari_cache      = None
_cari_cache_time = 0
CACHE_SURE       = 900  # 15 dakika

# ── RATE LIMIT ────────────────────────────────────────────────────────
_son_istek_zamani = 0.0
MIN_ISTEK_ARASI   = 0.35


def _rate_limit_bekle():
    global _son_istek_zamani
    gecen = time.time() - _son_istek_zamani
    if gecen < MIN_ISTEK_ARASI:
        time.sleep(MIN_ISTEK_ARASI - gecen)
    _son_istek_zamani = time.time()


def _token_al() -> dict:
    logger.info("Paraşüt'ten yeni token alınıyor...")
    response = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type":    "password",
            "client_id":     PARASUT_CLIENT_ID,
            "client_secret": PARASUT_CLIENT_SECRET,
            "username":      PARASUT_USERNAME,
            "password":      PARASUT_PASSWORD,
            "redirect_uri":  "urn:ietf:wg:oauth:2.0:oob"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Paraşüt token hatası: {response.status_code}")
    logger.info("Paraşüt token alındı")
    return response.json()


def _token_yenile(refresh_token: str) -> dict:
    logger.info("Paraşüt token yenileniyor...")
    response = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type":    "refresh_token",
            "client_id":     PARASUT_CLIENT_ID,
            "client_secret": PARASUT_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "redirect_uri":  "urn:ietf:wg:oauth:2.0:oob"
        }
    )
    if response.status_code != 200:
        return _token_al()
    logger.info("Paraşüt token yenilendi")
    return response.json()


def get_token() -> str:
    global _access_token, _refresh_token, _token_expires_at
    if _access_token is None:
        veri = _token_al()
    elif time.time() > _token_expires_at - 60:
        veri = _token_yenile(_refresh_token)
    else:
        return _access_token
    _access_token     = veri["access_token"]
    _refresh_token    = veri["refresh_token"]
    _token_expires_at = time.time() + veri["expires_in"]
    return _access_token


def _istek_yap(method: str, url: str, headers: dict, **kwargs) -> requests.Response:
    """Rate limit beklemesi + exponential backoff retry (429/503/504)."""
    _rate_limit_bekle()
    for deneme in range(4):
        response = requests.request(method, url, headers=headers, **kwargs)
        if response.status_code not in (429, 503, 504):
            return response
        retry_after = response.headers.get("Retry-After")
        bekleme     = float(retry_after) if retry_after else (2 ** deneme)
        logger.warning(
            f"Paraşüt {response.status_code} — {deneme + 1}. deneme, "
            f"{bekleme:.1f}s bekleniyor... (URL: {url})"
        )
        time.sleep(bekleme)
    return response


def parasut_get(endpoint: str, params: dict = None) -> dict:
    token    = get_token()
    url      = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "GET", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params=params
    )
    if response.status_code != 200:
        raise Exception(f"Paraşüt API hatası: {response.status_code} — {response.text[:200]}")
    return response.json()


def parasut_post(endpoint: str, veri: dict) -> dict:
    token    = get_token()
    url      = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "POST", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"},
        json=veri
    )
    if response.status_code not in [200, 201]:
        raise Exception(f"Paraşüt API hatası: {response.status_code} — {response.text[:200]}")
    return response.json()


def parasut_delete(endpoint: str) -> bool:
    token    = get_token()
    url      = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "DELETE", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"}
    )
    if response.status_code not in [200, 204]:
        raise Exception(f"Paraşüt DELETE hatası: {response.status_code} — {response.text[:200]}")
    return True


# ── CARİ CACHE ────────────────────────────────────────────────────────

def _cari_listesi_api() -> list:
    cariler = []
    page    = 1
    while page <= 100:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/contacts",
            params={"page[size]": 25, "page[number]": page}
        )
        data = sonuc.get("data", [])
        if not data:
            break
        for c in data:
            attr = c.get("attributes", {})
            try:
                bakiye_raw = attr.get("balance")
                bakiye     = float(bakiye_raw) if bakiye_raw is not None and bakiye_raw != "" else 0.0
            except (ValueError, TypeError):
                bakiye = 0.0
            cariler.append({
                "id":            c["id"],
                "ad":            attr.get("name", "-"),
                "email":         attr.get("email", "-"),
                "telefon":       attr.get("phone", "-"),
                "bakiye":        bakiye,
                "vergi_no":      attr.get("tax_number", "-"),
                "adres":         attr.get("address", "-"),
                "vergi_dairesi": attr.get("tax_office", "-"),
            })
        if len(data) < 25:
            break
        page += 1
        time.sleep(0.1)
    logger.info(f"Paraşüt'ten {len(cariler)} cari çekildi ({page - 1} sayfa)")
    return cariler


def cari_cache_yenile() -> list:
    global _cari_cache, _cari_cache_time
    _cari_cache      = _cari_listesi_api()
    _cari_cache_time = time.time()
    return _cari_cache


def cari_listesi_getir(force_refresh: bool = False) -> list:
    global _cari_cache, _cari_cache_time
    if not force_refresh and _cari_cache and time.time() - _cari_cache_time < CACHE_SURE:
        return _cari_cache
    try:
        return cari_cache_yenile()
    except Exception as e:
        logger.error(f"Cari cache yenileme hatası: {e}")
        return _cari_cache or []


# ── CARİ EŞLEŞTIRME — Fuzzy Matching ─────────────────────────────────

_GEREKSIZ_KELIMELER = {"A.S", "A.Ş", "LTD", "ŞTİ", "STI", "SAN", "TIC", "TİC", "VE", "GERI", "DONUSUM", "AS"}
_FUZZY_ESIK         = 0.72


def _normalize_cari_adi(ad: str) -> str:
    ad        = ad.upper().strip()
    ad        = re.sub(r'[().،,]', ' ', ad)
    kelimeler = [k for k in ad.split() if k not in _GEREKSIZ_KELIMELER]
    return " ".join(kelimeler)


def _fuzzy_skor(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def cari_id_bul(firma_adi: str) -> str | None:
    if not firma_adi:
        return None
    firma_upper = firma_adi.upper().strip()
    firma_norm  = _normalize_cari_adi(firma_adi)
    cariler     = cari_listesi_getir()

    for c in cariler:
        if c["ad"].upper() == firma_upper:
            logger.info(f"Cari tam eşleşme (cache): {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    try:
        kelimeler = [k for k in firma_norm.split() if k not in _GEREKSIZ_KELIMELER]
        arama     = " ".join(kelimeler[:2]) if kelimeler else firma_adi
        sonuc     = parasut_get(
            f"/{PARASUT_COMPANY_ID}/contacts",
            params={"filter[name]": arama, "page[size]": 25}
        )
        api_cariler = sonuc.get("data", [])
        for item in api_cariler:
            ad      = item.get("attributes", {}).get("name", "")
            cari_id = item.get("id")
            if ad.upper() == firma_upper:
                logger.info(f"Cari tam eşleşme (API): {firma_adi} → {ad} (ID: {cari_id})")
                return cari_id
        if api_cariler:
            item    = api_cariler[0]
            ad      = item.get("attributes", {}).get("name", "")
            cari_id = item.get("id")
            logger.info(f"Cari API ilk sonuç: {firma_adi} → {ad} (ID: {cari_id})")
            return cari_id
    except Exception as e:
        logger.warning(f"API cari arama hatası: {e}")

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

    en_iyi_skor = 0.0
    en_iyi_cari = None
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

    logger.warning(f"Cari bulunamadı: {firma_adi} (en iyi fuzzy skor: {en_iyi_skor:.2f})")
    return None


# ── SORGU FONKSİYONLARI ───────────────────────────────────────────────

def cari_detay_getir(cari_id: str) -> dict:
    try:
        sonuc = parasut_get(f"/{PARASUT_COMPANY_ID}/contacts/{cari_id}")
        attr  = sonuc.get("data", {}).get("attributes", {})
        try:
            bakiye_raw = attr.get("balance")
            bakiye     = float(bakiye_raw) if bakiye_raw is not None and bakiye_raw != "" else 0.0
        except (ValueError, TypeError):
            bakiye = 0.0
        return {
            "id":            cari_id,
            "ad":            attr.get("name", "-"),
            "email":         attr.get("email", "-"),
            "telefon":       attr.get("phone", "-"),
            "adres":         attr.get("address", "-"),
            "vergi_no":      attr.get("tax_number", "-"),
            "vergi_dairesi": attr.get("tax_office", "-"),
            "bakiye":        bakiye,
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
    durum: str = None,          # "paid" | "not_paid" | "overdue" | None=hepsi
) -> dict:
    """
    Carinin faturalarını sayfalı ve filtrelenmiş olarak getirir.

    Dönüş:
        {
            "faturalar": [...],
            "toplam": int,          # bu sayfadaki kayıt sayısı
            "sayfa": int,
            "sonraki_sayfa_var": bool
        }

    Parametreler:
        limit     : Sayfa başına kayıt (max 25, Paraşüt limiti)
        sayfa     : Kaçıncı sayfa (1'den başlar)
        baslangic : "YYYY-MM-DD" — bu tarihten itibaren
        bitis     : "YYYY-MM-DD" — bu tarihe kadar
        durum     : Ödeme durumu filtresi
    """
    try:
        params = {
            "filter[contact_id]": cari_id,
            "page[size]":         min(limit, 25),
            "page[number]":       sayfa,
            "sort":               "-issue_date",
            "include":            "contact"
        }
        if baslangic:
            params["filter[issue_date_gte]"] = baslangic
        if bitis:
            params["filter[issue_date_lte]"] = bitis
        if durum:
            params["filter[payment_status]"] = durum

        sonuc     = parasut_get(f"/{PARASUT_COMPANY_ID}/sales_invoices", params=params)
        data      = sonuc.get("data", [])
        meta      = sonuc.get("meta", {})
        # Paraşüt toplam kayıt sayısını meta'da döndürür
        toplam_kayit = int(meta.get("total_count", len(data)))

        faturalar = []
        for f in data:
            attr = f.get("attributes", {})
            try:
                kalan = float(attr.get("remaining") or 0)
            except (ValueError, TypeError):
                kalan = 0.0
            try:
                tutar = float(attr.get("gross_total") or 0)
            except (ValueError, TypeError):
                tutar = 0.0
            aciklama    = attr.get("description") or ""
            plaka       = None
            plaka_match = re.search(r'\b(\d{2}[A-ZÇŞĞÜÖİ]{1,3}\d{2,4})\b', aciklama)
            if plaka_match:
                plaka = plaka_match.group(1)
            irsaliye_ids = [
                d["id"] for d in f.get("relationships", {}).get("shipment_documents", {}).get("data", [])
            ]
            irsaliye_id = None
            m = re.search(r'[İI]rsaliye\s+(\d{8,})', aciklama)
            if m:
                irsaliye_id = m.group(1)
            faturalar.append({
                "id":           f["id"],
                "tarih":        attr.get("issue_date", "-"),
                "vade":         attr.get("due_date", "-"),
                "tutar":        tutar,
                "kalan":        kalan,
                "durum":        attr.get("payment_status", "-"),
                "aciklama":     aciklama or "-",
                "plaka":        plaka,
                "irsaliye_id":  irsaliye_id or (irsaliye_ids[0] if irsaliye_ids else None),
                "irsaliye_ids": irsaliye_ids,
            })

        return {
            "faturalar":          faturalar,
            "toplam":             len(faturalar),
            "toplam_kayit":       toplam_kayit,
            "sayfa":              sayfa,
            "sonraki_sayfa_var":  len(faturalar) == min(limit, 25),
        }
    except Exception as e:
        logger.error(f"Cari fatura hatası: {e}")
        return {"faturalar": [], "toplam": 0, "toplam_kayit": 0, "sayfa": sayfa, "sonraki_sayfa_var": False}


def fatura_detay_getir(fatura_id: str) -> dict:
    try:
        sonuc    = parasut_get(
            f"/{PARASUT_COMPANY_ID}/sales_invoices/{fatura_id}",
            params={"include": "contact,details,details.product,payments"}
        )
        data     = sonuc.get("data", {})
        attr     = data.get("attributes", {})
        included = sonuc.get("included", [])
        detail_ids  = {d["id"] for d in data.get("relationships", {}).get("details", {}).get("data", [])}
        kalemler    = []
        for inc in included:
            if inc.get("type") == "sales_invoice_details" and inc["id"] in detail_ids:
                a = inc.get("attributes", {})
                try:
                    miktar      = float(a.get("quantity") or 0)
                    birim_fiyat = float(a.get("unit_price") or 0)
                    tutar_kalem = miktar * birim_fiyat
                except (ValueError, TypeError):
                    miktar = birim_fiyat = tutar_kalem = 0.0
                kalemler.append({"aciklama": a.get("description") or "Hurda", "miktar": miktar,
                                  "birim_fiyat": birim_fiyat, "tutar": tutar_kalem})
        payment_ids = {p["id"] for p in data.get("relationships", {}).get("payments", {}).get("data", [])}
        tahsilatlar = []
        for inc in included:
            if inc.get("type") == "payments" and inc["id"] in payment_ids:
                a = inc.get("attributes", {})
                tahsilatlar.append({"tarih": a.get("date", "-"), "tutar": float(a.get("amount", 0)),
                                     "aciklama": a.get("description", "-")})
        irsaliye_ids = [d["id"] for d in data.get("relationships", {}).get("shipment_documents", {}).get("data", [])]
        try:
            tutar = float(attr.get("gross_total") or 0)
            kalan = float(attr.get("remaining") or 0)
        except (ValueError, TypeError):
            tutar = kalan = 0.0
        return {"id": fatura_id, "tarih": attr.get("issue_date", "-"), "vade": attr.get("due_date", "-"),
                "tutar": tutar, "kalan": kalan, "durum": attr.get("payment_status", "-"),
                "aciklama": attr.get("description", "-"), "kalemler": kalemler,
                "tahsilatlar": tahsilatlar, "irsaliye_ids": irsaliye_ids}
    except Exception as e:
        logger.error(f"Fatura detay hatası: {e}")
        return {}


def cari_irsaliyeleri_getir(cari_id: str, limit: int = 10) -> list:
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/shipment_documents",
            params={"filter[contact_id]": cari_id, "page[size]": limit, "sort": "-issue_date"}
        )
        return [{"id": i["id"], "tarih": i.get("attributes", {}).get("issue_date", "-"),
                 "aciklama": i.get("attributes", {}).get("description", "-"),
                 "durum": i.get("attributes", {}).get("status", "-")}
                for i in sonuc.get("data", [])]
    except Exception as e:
        logger.error(f"Cari irsaliye hatası: {e}")
        return []


def acik_faturalar_getir() -> list:
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/sales_invoices",
            params={"filter[payment_status]": "overdue,not_paid", "page[size]": 25, "sort": "-issue_date"}
        )
        faturalar = []
        for f in sonuc.get("data", []):
            attr     = f.get("attributes", {})
            aciklama = attr.get("description") or ""
            irsaliye_id = None
            m = re.search(r'[İI]rsaliye\s+(\d{8,})', aciklama)
            if m:
                irsaliye_id = m.group(1)
            faturalar.append({"id": f["id"], "tarih": attr.get("issue_date", "-"),
                               "vade": attr.get("due_date", "-"), "tutar": attr.get("gross_total", 0),
                               "kalan": attr.get("remaining", 0), "aciklama": aciklama or "-",
                               "irsaliye_id": irsaliye_id})
        return faturalar
    except Exception as e:
        logger.error(f"Açık fatura hatası: {e}")
        return []


def hesap_ozeti_getir() -> dict:
    """
    DÜZELTME: Eskiden force_refresh=True kullanılıyordu — her sorguda
    tüm cariler Paraşüt'ten çekiliyordu (~30 saniye, event loop donuyordu).
    Artık cache kullanılıyor. Güncel bakiye için /api/cariler?refresh=true.
    """
    try:
        cariler         = cari_listesi_getir()  # force_refresh kaldırıldı
        toplam_alacak   = sum(c["bakiye"] for c in cariler if c["bakiye"] > 0)
        toplam_odeyecek = sum(abs(c["bakiye"]) for c in cariler if c["bakiye"] < 0)
        return {"toplam_cari": len(cariler), "toplam_alacak": toplam_alacak, "toplam_borclu": toplam_odeyecek}
    except Exception as e:
        logger.error(f"Hesap özeti hatası: {e}")
        return {}


def islem_gecmisi_getir(cari_id: str, baslangic: str = None, bitis: str = None) -> dict:
    try:
        cari                 = cari_detay_getir(cari_id)
        bakiye               = cari.get("bakiye", 0.0)
        islemler             = []
        eklenen_tahsilat_ids = set()
        page = 1
        while page <= 50:
            fatura_sonuc = parasut_get(
                f"/{PARASUT_COMPANY_ID}/sales_invoices",
                params={"filter[contact_id]": cari_id, "include": "payments",
                        "page[size]": 25, "page[number]": page, "sort": "-issue_date"}
            )
            faturalar   = fatura_sonuc.get("data", [])
            included    = fatura_sonuc.get("included", [])
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
                        islemler.append({"id": f.get("id"), "tarih": tarih, "tur": "sales_invoice",
                                          "aciklama": attr.get("description") or "Satış Faturası",
                                          "tutar": tutar, "durum": attr.get("payment_status", "-"),
                                          "kalan": float(attr.get("remaining") or 0)})
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
                    islemler.append({"id": pid, "tarih": ptarih, "tur": "payment",
                                      "aciklama": pattr.get("notes") or pattr.get("description") or "Tahsilat",
                                      "tutar": -ptutar, "durum": "paid", "kalan": 0})
            if len(faturalar) < 25:
                break
            page += 1

        islemler.sort(key=lambda x: x.get("tarih", ""), reverse=True)
        logger.info(f"İşlem geçmişi: cari_id={cari_id} toplam={len(islemler)} işlem")
        return {"cari_id": cari_id, "cari_adi": cari.get("ad", "-"), "bakiye": bakiye, "islemler": islemler}
    except Exception as e:
        logger.error(f"İşlem geçmişi hatası: {e}")
        return {"bakiye": 0, "islemler": []}


def cari_olustur(firma_adi: str, email: str = None, telefon: str = None,
                 vergi_no: str = None, vergi_dairesi: str = None, adres: str = None) -> dict:
    try:
        attributes = {"name": firma_adi, "contact_type": "company", "account_type": "customer"}
        if email:         attributes["email"]      = email
        if telefon:       attributes["phone"]       = telefon
        if vergi_no:      attributes["tax_number"]  = vergi_no
        if vergi_dairesi: attributes["tax_office"]  = vergi_dairesi
        if adres:         attributes["address"]     = adres
        istek   = {"data": {"type": "contacts", "attributes": attributes}}
        sonuc   = parasut_post(f"/{PARASUT_COMPANY_ID}/contacts", istek)
        cari_id = sonuc["data"]["id"]
        logger.info(f"Yeni cari oluşturuldu: {firma_adi} (ID: {cari_id})")
        cari_cache_yenile()
        return {"basarili": True, "id": cari_id, "ad": firma_adi}
    except Exception as e:
        logger.error(f"Cari oluşturma hatası: {e}")
        return {"basarili": False, "hata": str(e)}


# ── KASA HESABI ───────────────────────────────────────────────────────
_kasa_hesabi_id = None

def _kasa_hesabi_bul() -> str | None:
    global _kasa_hesabi_id
    if _kasa_hesabi_id:
        return _kasa_hesabi_id
    try:
        sonuc = parasut_get(f"/{PARASUT_COMPANY_ID}/accounts")
        for hesap in sonuc.get("data", []):
            attr = hesap.get("attributes", {})
            if attr.get("account_type") in ("cash", "bank"):
                logger.info(f"Kasa hesabı bulundu: {hesap['id']} ({attr.get('name')})")
                _kasa_hesabi_id = hesap["id"]
                return _kasa_hesabi_id
        logger.warning("Kasa/banka hesabı bulunamadı")
        return None
    except Exception as e:
        logger.error(f"Kasa hesabı bulunamadı: {e}")
        return None


def fatura_tahsilat_ekle(fatura_id: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    try:
        from datetime import datetime
        bugun    = tarih or datetime.now().strftime("%Y-%m-%d")
        hesap_id = _kasa_hesabi_bul()
        if not hesap_id:
            return {"basarili": False, "hata": "Kasa/banka hesabı bulunamadı."}
        istek = {"data": {"type": "payments", "attributes": {
            "description": aciklama or "Tahsilat", "date": bugun,
            "amount": tutar, "account_id": int(hesap_id)
        }}}
        try:
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices/{fatura_id}/payments", istek)
        except Exception as e:
            istek["data"]["attributes"]["amount"] = str(tutar)
            logger.warning(f"Tahsilat v2 deneniyor: {e}")
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices/{fatura_id}/payments", istek)
        odeme_id = sonuc["data"]["id"]
        logger.info(f"Tahsilat eklendi: fatura={fatura_id} tutar={tutar:,.0f} TL")
        try:
            cari_cache_yenile()
        except Exception:
            pass
        return {"basarili": True, "id": odeme_id, "fatura_id": fatura_id, "tutar": tutar, "tarih": bugun}
    except Exception as e:
        logger.error(f"Fatura tahsilat hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def tahsilat_ekle(cari_adi: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    """
    Carinin açık faturalarına tahsilat ekler. Birden fazla faturaya böler.
    Kısmi başarı durumunda basarisiz_fatura sayısı döner — frontend uyarmalı.
    """
    try:
        cari_id = cari_id_bul(cari_adi)
        if not cari_id:
            return {"basarili": False, "hata": f"'{cari_adi}' adlı cari bulunamadı."}
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/sales_invoices",
            params={"filter[contact_id]": cari_id, "filter[payment_status]": "overdue,not_paid",
                    "page[size]": 25, "sort": "issue_date"}
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

        kalan_tutar      = float(tutar)
        odenen_faturalar = []
        toplam_odenen    = 0.0
        basarisiz_sayisi = 0

        for fatura in faturalar:
            if kalan_tutar <= 0:
                break
            odeme_miktari = min(kalan_tutar, fatura["kalan"])
            if odeme_miktari <= 0:
                continue
            odeme_sonuc = fatura_tahsilat_ekle(
                fatura_id=fatura["id"], tutar=odeme_miktari,
                aciklama=aciklama or f"{cari_adi} tahsilat", tarih=tarih
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
            return {"basarili": True, "cari": cari_adi, "tutar": toplam_odenen, "tarih": tarih or "",
                    "odenen_faturalar": odenen_faturalar,
                    "kalan_odenmedi": kalan_tutar if kalan_tutar > 0.01 else 0,
                    "basarisiz_fatura": basarisiz_sayisi}
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
) -> dict:
    """
    Bekleyen irsaliyeleri sayfalı ve tarih filtreli getirir.

    Parametreler:
        baslangic  : "YYYY-MM-DD" — bu tarihten itibaren
        bitis      : "YYYY-MM-DD" — bu tarihe kadar
        sayfa      : Kaçıncı sayfa (1'den başlar)
        sayfa_basi : Sayfa başına irsaliye (max 25)

    Dönüş:
        {
            "irsaliyeler": {contact_id: [...]},
            "sonraki_sayfa_var": bool,
            "sayfa": int
        }
    """
    try:
        gruplu = {}
        params = {
            "page[size]":   min(sayfa_basi, 25),
            "page[number]": sayfa,
            "sort":         "-issue_date",
            "include":      "stock_movements,contact"
        }
        if baslangic:
            params["filter[issue_date_gte]"] = baslangic
        if bitis:
            params["filter[issue_date_lte]"] = bitis

        sonuc    = parasut_get(f"/{PARASUT_COMPANY_ID}/shipment_documents", params=params)
        data     = sonuc.get("data", [])
        included = sonuc.get("included", [])

        sm_map      = {inc["id"]: inc.get("attributes", {}) for inc in included if inc.get("type") == "stock_movements"}
        contact_map = {inc["id"]: inc.get("attributes", {}).get("name", "-") for inc in included if inc.get("type") == "contacts"}

        for i in data:
            attr         = i.get("attributes", {})
            rels         = i.get("relationships", {})
            contact_data = rels.get("contact", {}).get("data", {})
            contact_id   = contact_data.get("id") if contact_data else None
            if not contact_id:
                continue
            sm_rels  = rels.get("stock_movements", {}).get("data", [])
            kalemler = []
            for sm_ref in sm_rels:
                sm_attr = sm_map.get(sm_ref["id"], {})
                miktar  = float(sm_attr.get("quantity", 0))
                if miktar > 0:
                    desc        = sm_attr.get("description") or ""
                    detail_no   = sm_attr.get("detail_no", len(kalemler) + 1)
                    malzeme_adi = f"Malzeme {detail_no}"
                    if desc:
                        try:
                            norm     = desc.replace(' - ', '—').replace('–', '—')
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
            irs = {"id": i["id"], "contact_id": contact_id, "cari_adi": contact_map.get(contact_id, "-"),
                   "tarih": attr.get("issue_date", "-"), "aciklama": attr.get("description", "-"),
                   "kalemler": kalemler, "toplam_kg": sum(k["miktar_kg"] for k in kalemler)}
            if contact_id not in gruplu:
                gruplu[contact_id] = []
            gruplu[contact_id].append(irs)

        toplam            = sum(len(v) for v in gruplu.values())
        sonraki_sayfa_var = len(data) == min(sayfa_basi, 25)
        logger.info(f"Bekleyen irsaliyeler: {toplam} kayıt, sayfa={sayfa}, sonraki={sonraki_sayfa_var}")
        return {
            "irsaliyeler":      gruplu,
            "sonraki_sayfa_var": sonraki_sayfa_var,
            "sayfa":            sayfa,
        }
    except Exception as e:
        logger.error(f"Tüm bekleyen irsaliye hatası: {e}")
        return {"irsaliyeler": {}, "sonraki_sayfa_var": False, "sayfa": sayfa}


def cari_bekleyen_irsaliyeleri_getir(cari_adi: str) -> list:
    try:
        cari_id = cari_id_bul(cari_adi)
        if not cari_id:
            return []
        sonuc    = parasut_get(
            f"/{PARASUT_COMPANY_ID}/shipment_documents",
            params={"filter[contact_id]": cari_id, "page[size]": 25, "sort": "-issue_date", "include": "stock_movements"}
        )
        included = sonuc.get("included", [])
        sm_map   = {inc["id"]: inc.get("attributes", {}) for inc in included if inc.get("type") == "stock_movements"}
        irsaliyeler = []
        for i in sonuc.get("data", []):
            attr     = i.get("attributes", {})
            rels     = i.get("relationships", {})
            sm_rels  = rels.get("stock_movements", {}).get("data", [])
            kalemler = []
            for sm_ref in sm_rels:
                sm_attr = sm_map.get(sm_ref["id"], {})
                miktar  = float(sm_attr.get("quantity", 0))
                if miktar > 0:
                    desc        = sm_attr.get("description") or ""
                    detail_no   = sm_attr.get("detail_no", len(kalemler) + 1)
                    malzeme_adi = f"Malzeme {detail_no}"
                    if desc:
                        try:
                            norm     = desc.replace(' - ', '—').replace('–', '—')
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
            irsaliyeler.append({"id": i["id"], "tarih": attr.get("issue_date", "-"),
                                  "aciklama": attr.get("description", "-"), "kalemler": kalemler,
                                  "toplam_kg": sum(k["miktar_kg"] for k in kalemler)})
        logger.info(f"{cari_adi} için {len(irsaliyeler)} irsaliye bulundu")
        return irsaliyeler
    except Exception as e:
        logger.error(f"Bekleyen irsaliye hatası: {e}")
        return []


def irsaliye_detay_getir(irsaliye_id: str) -> dict:
    try:
        sonuc    = parasut_get(
            f"/{PARASUT_COMPANY_ID}/shipment_documents/{irsaliye_id}",
            params={"include": "stock_movements,stock_movements.product"}
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
        return {"id": irsaliye_id, "tarih": attr.get("issue_date", "-"),
                "aciklama": attr.get("description", "-"), "kalemler": kalemler,
                "toplam_kg": sum(k["miktar_kg"] for k in kalemler)}
    except Exception as e:
        logger.error(f"İrsaliye detay hatası: {e}")
        return {}


# ── ASYNC WRAPPER'LAR ─────────────────────────────────────────────────
# FastAPI async endpoint'lerinden senkron Paraşüt fonksiyonlarını
# event loop'u bloklamadan çağırmak için kullan.
#
# Örnek main.py'de:
#   cariler = await async_cari_listesi_getir()
#   sonuc   = await async_parasut_post(endpoint, veri)

async def async_cari_listesi_getir(force_refresh: bool = False) -> list:
    return await asyncio.to_thread(cari_listesi_getir, force_refresh)

async def async_cari_cache_yenile() -> list:
    return await asyncio.to_thread(cari_cache_yenile)

async def async_cari_id_bul(firma_adi: str) -> str | None:
    return await asyncio.to_thread(cari_id_bul, firma_adi)

async def async_parasut_post(endpoint: str, veri: dict) -> dict:
    return await asyncio.to_thread(parasut_post, endpoint, veri)

async def async_parasut_delete(endpoint: str) -> bool:
    return await asyncio.to_thread(parasut_delete, endpoint)

async def async_tum_bekleyen_irsaliyeleri_getir(
    baslangic: str = None, bitis: str = None, sayfa: int = 1, sayfa_basi: int = 25
) -> dict:
    return await asyncio.to_thread(tum_bekleyen_irsaliyeleri_getir, baslangic, bitis, sayfa, sayfa_basi)

async def async_hesap_ozeti_getir() -> dict:
    return await asyncio.to_thread(hesap_ozeti_getir)

async def async_cari_detay_getir(cari_id: str) -> dict:
    return await asyncio.to_thread(cari_detay_getir, cari_id)

async def async_islem_gecmisi_getir(cari_id: str, baslangic: str = None, bitis: str = None) -> dict:
    return await asyncio.to_thread(islem_gecmisi_getir, cari_id, baslangic, bitis)

async def async_tahsilat_ekle(cari_adi: str, tutar: float,
                               aciklama: str = None, tarih: str = None) -> dict:
    return await asyncio.to_thread(tahsilat_ekle, cari_adi, tutar, aciklama, tarih)

async def async_cari_faturalari_getir(
    cari_id: str, limit: int = 25, sayfa: int = 1,
    baslangic: str = None, bitis: str = None, durum: str = None
) -> dict:
    return await asyncio.to_thread(cari_faturalari_getir, cari_id, limit, sayfa, baslangic, bitis, durum)

async def async_fatura_detay_getir(fatura_id: str) -> dict:
    return await asyncio.to_thread(fatura_detay_getir, fatura_id)