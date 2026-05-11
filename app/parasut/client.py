# client.py — Paraşüt API İstemcisi

import re
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

# ── CARİ CACHE ────────────────────────────────────────────────────────
_cari_cache = None
_cari_cache_time = 0
CACHE_SURE = 900  # 15 dakika (5'ten artırıldı — 1500 cari için rate limit koruması)

# ── RATE LIMIT KORUMASI ───────────────────────────────────────────────
_son_istek_zamani = 0.0
MIN_ISTEK_ARASI = 0.35  # saniye — saniyede ~2.8 istek (güvenli limit)


def _rate_limit_bekle():
    """İki istek arasında minimum süre bekler."""
    global _son_istek_zamani
    gecen = time.time() - _son_istek_zamani
    if gecen < MIN_ISTEK_ARASI:
        time.sleep(MIN_ISTEK_ARASI - gecen)
    _son_istek_zamani = time.time()


def _token_al() -> dict:
    logger.info("Parasut'ten yeni token aliniyor...")
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
        raise Exception(f"Parasut token hatasi: {response.status_code}")
    logger.info("Parasut token alindi")
    return response.json()


def _token_yenile(refresh_token: str) -> dict:
    logger.info("Parasut token yenileniyor...")
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
    logger.info("Parasut token yenilendi")
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


def _istek_yap(method: str, url: str, headers: dict, **kwargs) -> requests.Response:
    """
    Tüm HTTP isteklerini saran merkezi fonksiyon.
    - Rate limit beklemesi uygular
    - 429/503/504 hatalarında exponential backoff ile retry yapar
    - Max 4 deneme: 1s → 2s → 4s
    """
    _rate_limit_bekle()

    for deneme in range(4):
        response = requests.request(method, url, headers=headers, **kwargs)

        if response.status_code not in (429, 503, 504):
            return response

        # Rate limit veya sunucu hatası — bekle ve tekrar dene
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            bekleme = float(retry_after)
        else:
            bekleme = (2 ** deneme)  # 1, 2, 4 saniye

        logger.warning(
            f"Parasut {response.status_code} — {deneme + 1}. deneme, "
            f"{bekleme:.1f}s bekleniyor... (URL: {url})"
        )
        time.sleep(bekleme)

    # 4 denemeden sonra son yanıtı döndür (çağıran handle eder)
    return response


def parasut_get(endpoint: str, params: dict = None) -> dict:
    token = get_token()
    url = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "GET", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params=params
    )
    if response.status_code != 200:
        raise Exception(f"Parasut API hatasi: {response.status_code} — {response.text[:200]}")
    return response.json()


def parasut_post(endpoint: str, veri: dict) -> dict:
    token = get_token()
    url = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "POST", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"},
        json=veri
    )
    if response.status_code not in [200, 201]:
        raise Exception(f"Parasut API hatasi: {response.status_code} — {response.text[:200]}")
    return response.json()


def parasut_delete(endpoint: str) -> bool:
    token = get_token()
    url = f"{PARASUT_BASE_URL}{endpoint}"
    response = _istek_yap(
        "DELETE", url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"}
    )
    if response.status_code not in [200, 204]:
        raise Exception(f"Parasut DELETE hatasi: {response.status_code} — {response.text[:200]}")
    return True


# ── CARİ CACHE SİSTEMİ ───────────────────────────────────────────────

def _cari_listesi_api() -> list:
    """Paraşüt'ten tüm carileri çeker (tüm sayfalar, rate limit korumalı)."""
    cariler = []
    page = 1
    while page <= 100:  # max 100 sayfa = 2500 cari güvenlik limiti
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
                bakiye = float(bakiye_raw) if bakiye_raw is not None and bakiye_raw != "" else 0.0
            except (ValueError, TypeError):
                bakiye = 0.0
            cariler.append({
                "id": c["id"],
                "ad": attr.get("name", "-"),
                "email": attr.get("email", "-"),
                "telefon": attr.get("phone", "-"),
                "bakiye": bakiye,
                "vergi_no": attr.get("tax_number", "-"),
                "adres": attr.get("address", "-"),
                "vergi_dairesi": attr.get("tax_office", "-"),
            })
        if len(data) < 25:
            break  # Son sayfa
        page += 1
        # _istek_yap zaten rate limit beklemesi yapıyor ama
        # sayfalama döngüsünde ek güvenlik için kısa ek bekleme
        time.sleep(0.1)

    logger.info(f"Parasut'ten {len(cariler)} cari cekildi ({page - 1} sayfa)")
    return cariler


def cari_cache_yenile():
    global _cari_cache, _cari_cache_time
    _cari_cache = _cari_listesi_api()
    _cari_cache_time = time.time()
    return _cari_cache


def cari_listesi_getir(force_refresh: bool = False) -> list:
    global _cari_cache, _cari_cache_time
    if not force_refresh and _cari_cache and time.time() - _cari_cache_time < CACHE_SURE:
        return _cari_cache
    try:
        return cari_cache_yenile()
    except Exception as e:
        logger.error(f"Cari cache yenileme hatasi: {e}")
        return _cari_cache or []


def cari_id_bul(firma_adi: str) -> str | None:
    if not firma_adi:
        return None
    firma_upper = firma_adi.upper().strip()

    # 1. Önce cache'de tam eşleşme dene (hızlı)
    cariler = cari_listesi_getir()
    for c in cariler:
        if c["ad"].upper() == firma_upper:
            logger.info(f"Cari tam eslestirme (cache): {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    # 2. Cache'de bulunamazsa Paraşüt API'sine filter[name] ile sor
    try:
        kelimeler = [k for k in firma_upper.split() if k not in {"A.S.", "LTD.", "ŞTİ.", "SAN.", "TİC.", "VE", "A.Ş"}]
        arama = " ".join(kelimeler[:2]) if kelimeler else firma_adi
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/contacts",
            params={"filter[name]": arama, "page[size]": 25}
        )
        api_cariler = sonuc.get("data", [])
        for item in api_cariler:
            ad = item.get("attributes", {}).get("name", "")
            cari_id = item.get("id")
            if ad.upper() == firma_upper:
                logger.info(f"Cari tam eslestirme (API): {firma_adi} → {ad} (ID: {cari_id})")
                return cari_id
        if api_cariler:
            item = api_cariler[0]
            ad = item.get("attributes", {}).get("name", "")
            cari_id = item.get("id")
            logger.info(f"Cari API eslestirme (ilk sonuç): {firma_adi} → {ad} (ID: {cari_id})")
            return cari_id
    except Exception as e:
        logger.warning(f"API cari arama hatasi: {e}")

    # 3. Son çare: cache'de içerik/kelime eşleştirmesi
    for c in cariler:
        cari_upper = c["ad"].upper()
        if firma_upper in cari_upper or cari_upper in firma_upper:
            logger.info(f"Cari icerik eslestirme (cache): {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    firma_kelimeler = set(firma_upper.split())
    for c in cariler:
        cari_kelimeler = set(c["ad"].upper().split())
        ortak = firma_kelimeler & cari_kelimeler
        genel = {"A.S", "LTD", "STI", "SAN", "TIC", "VE", "GERI", "DONUSUM"}
        anlamli_ortak = ortak - genel
        if len(anlamli_ortak) >= 2:
            logger.info(f"Cari kelime eslestirme (cache): {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    logger.warning(f"Cari bulunamadi: {firma_adi}")
    return None


# ── CHATBOT SORGU FONKSİYONLARI ──────────────────────────────────────

def cari_detay_getir(cari_id: str) -> dict:
    try:
        sonuc = parasut_get(f"/{PARASUT_COMPANY_ID}/contacts/{cari_id}")
        attr = sonuc.get("data", {}).get("attributes", {})
        try:
            bakiye_raw = attr.get("balance")
            bakiye = float(bakiye_raw) if bakiye_raw is not None and bakiye_raw != "" else 0.0
        except (ValueError, TypeError):
            bakiye = 0.0
        return {
            "id": cari_id,
            "ad": attr.get("name", "-"),
            "email": attr.get("email", "-"),
            "telefon": attr.get("phone", "-"),
            "adres": attr.get("address", "-"),
            "vergi_no": attr.get("tax_number", "-"),
            "vergi_dairesi": attr.get("tax_office", "-"),
            "bakiye": bakiye,
        }
    except Exception as e:
        logger.error(f"Cari detay hatasi: {e}")
        return {}


def cari_faturalari_getir(cari_id: str, limit: int = 10) -> list:
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/purchase_bills",
            params={
                "filter[supplier_id]": cari_id,
                "page[size]": limit,
                "sort": "-issue_date",
                "include": "supplier"
            }
        )
        faturalar = []
        for f in sonuc.get("data", []):
            attr = f.get("attributes", {})
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
            irsaliye_id = None
            plaka_match = re.search(r'\b(\d{2}[A-ZÇŞĞÜÖİ]{1,3}\d{2,4})\b', aciklama)
            if plaka_match:
                plaka = plaka_match.group(1)
            irsaliye_match = re.search(r'[İI]rsaliye\s+(\d{8,})', aciklama)
            if irsaliye_match:
                irsaliye_id = irsaliye_match.group(1)

            irsaliye_ids = [
                d["id"] for d in f.get("relationships", {}).get("shipment_documents", {}).get("data", [])
            ]

            faturalar.append({
                "id": f["id"],
                "tarih": attr.get("issue_date", "-"),
                "vade": attr.get("due_date", "-"),
                "tutar": tutar,
                "kalan": kalan,
                "durum": attr.get("payment_status", "-"),
                "aciklama": aciklama or "-",
                "plaka": plaka,
                "irsaliye_id": irsaliye_id or (irsaliye_ids[0] if irsaliye_ids else None),
                "irsaliye_ids": irsaliye_ids,
            })
        return faturalar
    except Exception as e:
        logger.error(f"Cari fatura hatasi: {e}")
        return []


def fatura_detay_getir(fatura_id: str) -> dict:
    """Fatura detayı: kalemler, tahsilatlar ve bağlı irsaliye ID'leri."""
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/purchase_bills/{fatura_id}",
            params={"include": "contact,details,details.product,payments"}
        )
        data = sonuc.get("data", {})
        attr = data.get("attributes", {})
        included = sonuc.get("included", [])

        detail_ids = {d["id"] for d in data.get("relationships", {}).get("details", {}).get("data", [])}
        kalemler = []
        for inc in included:
            if inc.get("type") == "sales_invoice_details" and inc["id"] in detail_ids:
                a = inc.get("attributes", {})
                try:
                    miktar = float(a.get("quantity") or 0)
                    birim_fiyat = float(a.get("unit_price") or 0)
                    tutar_kalem = miktar * birim_fiyat
                except (ValueError, TypeError):
                    miktar = birim_fiyat = tutar_kalem = 0.0
                kalemler.append({
                    "aciklama": a.get("description") or "Hurda",
                    "miktar": miktar,
                    "birim_fiyat": birim_fiyat,
                    "tutar": tutar_kalem,
                })

        payment_ids = {p["id"] for p in data.get("relationships", {}).get("payments", {}).get("data", [])}
        tahsilatlar = []
        for inc in included:
            if inc.get("type") == "payments" and inc["id"] in payment_ids:
                a = inc.get("attributes", {})
                tahsilatlar.append({
                    "tarih": a.get("date", "-"),
                    "tutar": float(a.get("amount", 0)),
                    "aciklama": a.get("description", "-"),
                })

        irsaliye_ids = [
            d["id"] for d in data.get("relationships", {}).get("shipment_documents", {}).get("data", [])
        ]

        try:
            tutar = float(attr.get("gross_total") or 0)
            kalan = float(attr.get("remaining") or 0)
        except (ValueError, TypeError):
            tutar = kalan = 0.0

        return {
            "id": fatura_id,
            "tarih": attr.get("issue_date", "-"),
            "vade": attr.get("due_date", "-"),
            "tutar": tutar,
            "kalan": kalan,
            "durum": attr.get("payment_status", "-"),
            "aciklama": attr.get("description", "-"),
            "kalemler": kalemler,
            "tahsilatlar": tahsilatlar,
            "irsaliye_ids": irsaliye_ids,
        }
    except Exception as e:
        logger.error(f"Fatura detay hatasi: {e}")
        return {}


def cari_irsaliyeleri_getir(cari_id: str, limit: int = 10) -> list:
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
        logger.error(f"Cari irsaliye hatasi: {e}")
        return []


def acik_faturalar_getir() -> list:
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/purchase_bills",
            params={"filter[payment_status]": "overdue,not_paid", "page[size]": 25, "sort": "-issue_date"}
        )
        faturalar = []
        for f in sonuc.get("data", []):
            attr = f.get("attributes", {})
            aciklama = attr.get("description") or ""
            irsaliye_id = None
            m = re.search(r'[İI]rsaliye\s+(\d{8,})', aciklama)
            if m:
                irsaliye_id = m.group(1)
            faturalar.append({
                "id": f["id"],
                "tarih": attr.get("issue_date", "-"),
                "vade": attr.get("due_date", "-"),
                "tutar": attr.get("gross_total", 0),
                "kalan": attr.get("remaining", 0),
                "aciklama": aciklama or "-",
                "irsaliye_id": irsaliye_id,
            })
        return faturalar
    except Exception as e:
        logger.error(f"Acik fatura hatasi: {e}")
        return []


def hesap_ozeti_getir() -> dict:
    try:
        cariler = cari_listesi_getir(force_refresh=True)
        toplam_alacak = sum(c["bakiye"] for c in cariler if c["bakiye"] > 0)
        toplam_odeyecek = sum(abs(c["bakiye"]) for c in cariler if c["bakiye"] < 0)
        return {
            "toplam_cari": len(cariler),
            "toplam_alacak": toplam_alacak,
            "toplam_borclu": toplam_odeyecek,
        }
    except Exception as e:
        logger.error(f"Hesap ozeti hatasi: {e}")
        return {}


def islem_gecmisi_getir(cari_id: str, baslangic: str = None, bitis: str = None) -> dict:
    """
    Belirli bir carinin işlem geçmişini getirir.
    Fatura listesini include=payments ile TEK istekte çeker — N+1 yok.
    Tarih filtresi Python tarafında yapılır.
    """
    try:
        cari = cari_detay_getir(cari_id)
        bakiye = cari.get("bakiye", 0.0)
        islemler = []
        eklenen_tahsilat_ids = set()

        # Tek istekte faturalar + tahsilatlar — include=payments
        page = 1
        while page <= 50:  # Pagination güvenliği: max 50 sayfa
            fatura_sonuc = parasut_get(
                f"/{PARASUT_COMPANY_ID}/purchase_bills",
                params={
                    "filter[supplier_id]": cari_id,
                    "include": "payments",
                    "page[size]": 25,
                    "page[number]": page,
                    "sort": "-issue_date"
                }
            )
            faturalar = fatura_sonuc.get("data", [])
            included = fatura_sonuc.get("included", [])

            # included'dan payment map'i oluştur — O(1) erişim için
            payment_map = {
                item["id"]: item
                for item in included
                if item.get("type") == "payments"
            }

            for f in faturalar:
                attr = f.get("attributes", {})
                tarih = attr.get("issue_date", "-")

                # Faturayı ekle
                if not baslangic or tarih >= baslangic:
                    if not bitis or tarih <= bitis:
                        try:
                            tutar = float(attr.get("gross_total") or 0)
                        except (ValueError, TypeError):
                            tutar = 0.0
                        islemler.append({
                            "id": f.get("id"),
                            "tarih": tarih,
                            "tur": "sales_invoice",
                            "aciklama": attr.get("description") or "Satış Faturası",
                            "tutar": tutar,
                            "durum": attr.get("payment_status", "-"),
                            "kalan": float(attr.get("remaining") or 0),
                        })

                # Bu faturanın tahsilatlarını ekle
                payment_ids = [
                    d["id"] for d in
                    f.get("relationships", {})
                    .get("payments", {}).get("data", [])
                ]
                for pid in payment_ids:
                    if pid in eklenen_tahsilat_ids:
                        continue
                    p = payment_map.get(pid)
                    if not p:
                        continue
                    eklenen_tahsilat_ids.add(pid)
                    pattr = p.get("attributes", {})
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
                        "id": pid,
                        "tarih": ptarih,
                        "tur": "payment",
                        "aciklama": pattr.get("notes") or pattr.get("description") or "Tahsilat",
                        "tutar": -ptutar,
                        "durum": "paid",
                        "kalan": 0,
                    })

            if len(faturalar) < 25:
                break
            page += 1

        # Tarihe göre sırala
        islemler.sort(key=lambda x: x.get("tarih", ""), reverse=True)

        logger.info(f"İşlem geçmişi: cari_id={cari_id} toplam={len(islemler)} işlem")
        return {
            "cari_id": cari_id,
            "cari_adi": cari.get("ad", "-"),
            "bakiye": bakiye,
            "islemler": islemler
        }
    except Exception as e:
        logger.error(f"İşlem geçmişi hatası: {e}")
        return {"bakiye": 0, "islemler": []}


def cari_olustur(firma_adi: str, email: str = None, telefon: str = None,
                 vergi_no: str = None, vergi_dairesi: str = None, adres: str = None) -> dict:
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
        logger.info(f"Yeni cari olusturuldu: {firma_adi} (ID: {cari_id})")
        cari_cache_yenile()
        return {"basarili": True, "id": cari_id, "ad": firma_adi}
    except Exception as e:
        logger.error(f"Cari olusturma hatasi: {e}")
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
                logger.info(f"Kasa hesabi bulundu: {hesap['id']} ({attr.get('name')})")
                _kasa_hesabi_id = hesap["id"]
                return _kasa_hesabi_id
        logger.warning("Kasa/banka hesabi bulunamadi")
        return None
    except Exception as e:
        logger.error(f"Kasa hesabi bulunamadi: {e}")
        return None


def fatura_odeme_ekle(fatura_id: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    """Paraşüt'te belirli bir alım faturasına ödeme ekler (purchase_bills payments).

    Hurda ALIM işlemi olduğu için faturalar purchase_bills'tir.
    Ödeme = senin kasandan para çıkışı (borç azalır).
    """
    try:
        from datetime import datetime
        bugun = tarih or datetime.now().strftime("%Y-%m-%d")

        hesap_id = _kasa_hesabi_bul()
        if not hesap_id:
            return {"basarili": False, "hata": "Kasa/banka hesabi bulunamadi."}

        istek = {
            "data": {
                "type": "payments",
                "attributes": {
                    "description": aciklama or "Ödeme",
                    "date": bugun,
                    "amount": tutar,
                    "account_id": int(hesap_id),
                }
            }
        }

        try:
            logger.info(f"Ödeme deneniyor: fatura={fatura_id} tutar={tutar:,.0f}")
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/purchase_bills/{fatura_id}/payments", istek)
        except Exception as e:
            istek["data"]["attributes"]["amount"] = str(tutar)
            logger.warning(f"Ödeme v2 deneniyor: {e}")
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/purchase_bills/{fatura_id}/payments", istek)

        odeme_id = sonuc["data"]["id"]
        logger.info(f"✅ Ödeme eklendi: fatura={fatura_id} tutar={tutar:,.0f} TL")

        try:
            cari_cache_yenile()
        except Exception:
            pass

        return {
            "basarili": True,
            "id": odeme_id,
            "fatura_id": fatura_id,
            "tutar": tutar,
            "tarih": bugun
        }
    except Exception as e:
        logger.error(f"Fatura ödeme hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def fatura_tahsilat_ekle(fatura_id: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    """Geriye dönük uyumluluk — fatura_odeme_ekle'yi çağırır."""
    return fatura_odeme_ekle(fatura_id, tutar, aciklama, tarih)


def odeme_ekle(cari_adi: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    """Belirli bir carinin açık alım faturalarına ödeme ekler. Birden fazla faturaya böler.

    Hurda ALIM işlemi — purchase_bills üzerine ödeme eklenir.
    """
    try:
        cari_id = cari_id_bul(cari_adi)
        if not cari_id:
            return {"basarili": False, "hata": f"'{cari_adi}' adli cari bulunamadi."}

        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/purchase_bills",
            params={
                "filter[supplier_id]": cari_id,
                "filter[payment_status]": "overdue,not_paid",
                "page[size]": 25,
                "sort": "issue_date"
            }
        )

        faturalar = []
        for f in sonuc.get("data", []):
            attr = f.get("attributes", {})
            kalan = float(attr.get("remaining") or attr.get("gross_total") or 0)
            if kalan > 0:
                faturalar.append({
                    "id": f["id"],
                    "tarih": attr.get("issue_date", "-"),
                    "tutar": float(attr.get("gross_total", 0)),
                    "kalan": kalan,
                })

        if not faturalar:
            return {"basarili": False, "hata": f"{cari_adi} icin acik fatura bulunamadi."}

        kalan_tutar = float(tutar)
        odenen_faturalar = []
        toplam_odenen = 0.0

        for fatura in faturalar:
            if kalan_tutar <= 0:
                break
            odeme_miktari = min(kalan_tutar, fatura["kalan"])
            if odeme_miktari <= 0:
                continue

            odeme_sonuc = fatura_odeme_ekle(
                fatura_id=fatura["id"],
                tutar=odeme_miktari,
                aciklama=aciklama or f"{cari_adi} tahsilat",
                tarih=tarih
            )
            # _istek_yap zaten rate limit beklemesi yapıyor, ek sleep gerekmez

            if odeme_sonuc.get("basarili"):
                odenen_faturalar.append({"fatura_id": fatura["id"], "odenen": odeme_miktari})
                toplam_odenen += odeme_miktari
                kalan_tutar -= odeme_miktari
                logger.info(f"Tahsilat: {cari_adi} — {odeme_miktari:,.0f} TL → fatura {fatura['id']}")
            else:
                logger.warning(f"Fatura {fatura['id']} tahsilat basarisiz: {odeme_sonuc.get('hata')}")

        if toplam_odenen > 0:
            return {
                "basarili": True,
                "cari": cari_adi,
                "tutar": toplam_odenen,
                "tarih": tarih or "",
                "odenen_faturalar": odenen_faturalar,
                "kalan_odenmedi": kalan_tutar if kalan_tutar > 0.01 else 0
            }
        else:
            return {"basarili": False, "hata": "Hicbir faturaya tahsilat eklenemedi."}

    except Exception as e:
        logger.error(f"Tahsilat ekleme hatasi: {e}")
        return {"basarili": False, "hata": str(e)}


def tum_bekleyen_irsaliyeleri_getir(sayfa_no: int = 1) -> dict:
    """
    Belirtilen sayfayı çeker (25 irsaliye/sayfa).
    contact ID'ye göre gruplar.
    Returns: {contact_id: [irsaliye, ...], "_meta": {...}}
    """
    try:
        gruplu = {}
        page = sayfa_no

        while page <= sayfa_no:  # Sadece istenen sayfayı çek
            sonuc = parasut_get(
                f"/{PARASUT_COMPANY_ID}/shipment_documents",
                params={
                    "page[size]": 25,
                    "page[number]": page,
                    "sort": "-issue_date",
                    "include": "stock_movements,contact"
                }
            )

            data = sonuc.get("data", [])
            if not data:
                break

            included = sonuc.get("included", [])
            sm_map = {
                inc["id"]: inc.get("attributes", {})
                for inc in included
                if inc.get("type") == "stock_movements"
            }
            contact_map = {
                inc["id"]: inc.get("attributes", {}).get("name", "-")
                for inc in included
                if inc.get("type") == "contacts"
            }

            for i in data:
                attr = i.get("attributes", {})
                rels = i.get("relationships", {})

                contact_data = rels.get("contact", {}).get("data", {})
                contact_id = contact_data.get("id") if contact_data else None
                if not contact_id:
                    continue

                sm_rels = rels.get("stock_movements", {}).get("data", [])
                kalemler = []
                for sm_ref in sm_rels:
                    sm_attr = sm_map.get(sm_ref["id"], {})
                    miktar = float(sm_attr.get("quantity", 0))
                    if miktar > 0:
                        desc = sm_attr.get("description") or ""
                        detail_no = sm_attr.get("detail_no", len(kalemler) + 1)
                        malzeme_adi = f"Malzeme {detail_no}"
                        if desc:
                            try:
                                norm = desc.replace(' - ', '—').replace('–', '—')
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

                irs = {
                    "id": i["id"],
                    "contact_id": contact_id,
                    "cari_adi": contact_map.get(contact_id, "-"),
                    "tarih": attr.get("issue_date", "-"),
                    "aciklama": attr.get("description", "-"),
                    "kalemler": kalemler,
                    "toplam_kg": sum(k["miktar_kg"] for k in kalemler),
                }

                if contact_id not in gruplu:
                    gruplu[contact_id] = []
                gruplu[contact_id].append(irs)

            if len(data) < 25:
                break  # Son sayfa
            page += 1

        # Toplam kayıt sayısını meta'dan al
        meta_data = sonuc.get("meta", {}) if 'sonuc' in dir() else {}
        toplam_kayit = meta_data.get("total_count", 0)
        toplam_sayfa = max(1, (toplam_kayit + 24) // 25) if toplam_kayit else 1

        toplam = sum(len(v) for k, v in gruplu.items() if k != "_meta")
        gruplu["_meta"] = {
            "toplam_sayfa": toplam_sayfa,
            "mevcut_sayfa": sayfa_no,
            "toplam_kayit": toplam_kayit,
            "bu_sayfada": toplam
        }
        logger.info(f"Bekleyen irsaliyeler: {toplam} bu sayfada, sayfa={sayfa_no}/{toplam_sayfa}")
        return gruplu

    except Exception as e:
        logger.error(f"Tum bekleyen irsaliye hatasi: {e}")
        return {}


def cari_bekleyen_irsaliyeleri_getir(cari_adi: str) -> list:
    """Belirli bir carinin bekleyen irsaliyelerini getirir."""
    try:
        cari_id = cari_id_bul(cari_adi)
        if not cari_id:
            return []

        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/shipment_documents",
            params={
                "filter[contact_id]": cari_id,
                "page[size]": 25,
                "sort": "-issue_date",
                "include": "stock_movements"
            }
        )

        included = sonuc.get("included", [])
        sm_map = {
            inc["id"]: inc.get("attributes", {})
            for inc in included
            if inc.get("type") == "stock_movements"
        }

        irsaliyeler = []
        for i in sonuc.get("data", []):
            attr = i.get("attributes", {})
            rels = i.get("relationships", {})
            sm_rels = rels.get("stock_movements", {}).get("data", [])

            kalemler = []
            for sm_ref in sm_rels:
                sm_attr = sm_map.get(sm_ref["id"], {})
                miktar = float(sm_attr.get("quantity", 0))
                if miktar > 0:
                    desc = sm_attr.get("description") or ""
                    detail_no = sm_attr.get("detail_no", len(kalemler) + 1)
                    malzeme_adi = f"Malzeme {detail_no}"
                    if desc:
                        try:
                            norm = desc.replace(' - ', '—').replace('–', '—')
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
                "id": i["id"],
                "tarih": attr.get("issue_date", "-"),
                "aciklama": attr.get("description", "-"),
                "kalemler": kalemler,
                "toplam_kg": sum(k["miktar_kg"] for k in kalemler),
            })

        logger.info(f"{cari_adi} icin {len(irsaliyeler)} irsaliye bulundu")
        return irsaliyeler

    except Exception as e:
        logger.error(f"Bekleyen irsaliye hatasi: {e}")
        return []


def irsaliye_detay_getir(irsaliye_id: str) -> dict:
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/shipment_documents/{irsaliye_id}",
            params={"include": "stock_movements,stock_movements.product"}
        )
        data = sonuc.get("data", {})
        attr = data.get("attributes", {})
        included = sonuc.get("included", [])
        sm_rels = data.get("relationships", {}).get("stock_movements", {}).get("data", [])
        sm_ids = {sm["id"] for sm in sm_rels}
        kalemler = []
        for inc in included:
            if inc.get("type") == "stock_movements" and inc["id"] in sm_ids:
                sm_attr = inc.get("attributes", {})
                miktar = float(sm_attr.get("quantity", 0))
                if miktar > 0:
                    kalemler.append({
                        "aciklama": sm_attr.get("description") or "Hurda",
                        "miktar_kg": miktar,
                    })
        return {
            "id": irsaliye_id,
            "tarih": attr.get("issue_date", "-"),
            "aciklama": attr.get("description", "-"),
            "kalemler": kalemler,
            "toplam_kg": sum(k["miktar_kg"] for k in kalemler),
        }
    except Exception as e:
        logger.error(f"Irsaliye detay hatasi: {e}")
        return {}


def satis_tahsilat_ekle(cari_adi: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    """Müşteriden tahsilat ekler — sales_invoices üzerine payment.

    Tahsilat = müşteri bize para ödedi, kasaya para girdi.
    Müşteriler: UHT, Kroman, İçdaş gibi hurda alanlar.
    """
    try:
        cari_id = cari_id_bul(cari_adi)
        if not cari_id:
            return {"basarili": False, "hata": f"'{cari_adi}' adlı cari bulunamadı."}

        # Müşterinin açık satış faturalarını getir
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/sales_invoices",
            params={
                "filter[contact_id]": cari_id,
                "filter[payment_status]": "overdue,not_paid",
                "page[size]": 25,
                "sort": "issue_date"
            }
        )

        faturalar = []
        for f in sonuc.get("data", []):
            attr = f.get("attributes", {})
            kalan = float(attr.get("remaining") or attr.get("gross_total") or 0)
            if kalan > 0:
                faturalar.append({"id": f["id"], "kalan": kalan})

        if not faturalar:
            return {"basarili": False, "hata": f"{cari_adi} için açık satış faturası bulunamadı."}

        kalan_tutar = float(tutar)
        toplam_odenen = 0.0

        for fatura in faturalar:
            if kalan_tutar <= 0:
                break
            odeme_miktari = min(kalan_tutar, fatura["kalan"])
            if odeme_miktari <= 0:
                continue

            hesap_id = _kasa_hesabi_bul()
            if not hesap_id:
                return {"basarili": False, "hata": "Kasa/banka hesabı bulunamadı."}

            from datetime import datetime
            bugun = tarih or datetime.now().strftime("%Y-%m-%d")
            istek = {
                "data": {
                    "type": "payments",
                    "attributes": {
                        "description": aciklama or f"{cari_adi} tahsilat",
                        "date": bugun,
                        "amount": odeme_miktari,
                        "account_id": int(hesap_id),
                    }
                }
            }

            try:
                sonuc2 = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices/{fatura['id']}/payments", istek)
                toplam_odenen += odeme_miktari
                kalan_tutar -= odeme_miktari
                logger.info(f"✅ Tahsilat: {cari_adi} — {odeme_miktari:,.0f} TL → fatura {fatura['id']}")
            except Exception as e:
                logger.warning(f"Fatura {fatura['id']} tahsilat başarısız: {e}")

        if toplam_odenen > 0:
            try:
                cari_cache_yenile()
            except Exception:
                pass
            return {"basarili": True, "cari": cari_adi, "tutar": toplam_odenen}
        else:
            return {"basarili": False, "hata": "Hiçbir faturaya tahsilat eklenemedi."}

    except Exception as e:
        logger.error(f"Satış tahsilat hatası: {e}")
        return {"basarili": False, "hata": str(e)}


def tahsilat_ekle(cari_adi: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    """Geriye dönük uyumluluk — odeme_ekle'yi çağırır."""
    return odeme_ekle(cari_adi, tutar, aciklama, tarih)