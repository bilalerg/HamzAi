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
CACHE_SURE = 60  # 1 dakika


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


def parasut_get(endpoint: str, params: dict = None) -> dict:
    token = get_token()
    url = f"{PARASUT_BASE_URL}{endpoint}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params=params
    )
    if response.status_code != 200:
        raise Exception(f"Parasut API hatasi: {response.status_code} — {response.text[:200]}")
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
        raise Exception(f"Parasut API hatasi: {response.status_code} — {response.text[:200]}")
    return response.json()


def parasut_delete(endpoint: str) -> bool:
    token = get_token()
    url = f"{PARASUT_BASE_URL}{endpoint}"
    response = requests.delete(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/vnd.api+json"}
    )
    if response.status_code not in [200, 204]:
        raise Exception(f"Parasut DELETE hatasi: {response.status_code} — {response.text[:200]}")
    return True


# ── CARİ CACHE SİSTEMİ ───────────────────────────────────────────────

def _cari_listesi_api() -> list:
    """Parasut'ten tum carileri ceker (tum sayfalar)."""
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
            break
        page += 1
    logger.info(f"Parasut'ten {len(cariler)} cari cekildi")
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
    cariler = cari_listesi_getir()

    # 1. Tam eslestirme
    for c in cariler:
        if c["ad"].upper() == firma_upper:
            logger.info(f"Cari tam eslestirme: {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    # 2. Icerik eslestirmesi
    for c in cariler:
        cari_upper = c["ad"].upper()
        if firma_upper in cari_upper or cari_upper in firma_upper:
            logger.info(f"Cari icerik eslestirme: {firma_adi} → {c['ad']} (ID: {c['id']})")
            return c["id"]

    # 3. Kelime bazli eslestirme
    firma_kelimeler = set(firma_upper.split())
    for c in cariler:
        cari_kelimeler = set(c["ad"].upper().split())
        ortak = firma_kelimeler & cari_kelimeler
        genel = {"A.S", "LTD", "STI", "SAN", "TIC", "VE", "GERI", "DONUSUM", "NAK", "TICARET"}
        anlamli_ortak = ortak - genel
        if len(anlamli_ortak) >= 2:
            logger.info(f"Cari kelime eslestirme: {firma_adi} → {c['ad']} (ID: {c['id']})")
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
            f"/{PARASUT_COMPANY_ID}/sales_invoices",
            params={
                "filter[contact_id]": cari_id,
                "page[size]": limit,
                "sort": "-issue_date",
                "include": "contact"
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

            # Açıklamadan plaka ve irsaliye ID çıkar
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
            f"/{PARASUT_COMPANY_ID}/sales_invoices/{fatura_id}",
            params={"include": "contact,details,details.product,payments"}
        )
        data = sonuc.get("data", {})
        attr = data.get("attributes", {})
        included = sonuc.get("included", [])

        # Fatura kalemleri
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

        # Tahsilatlar
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

        # Bağlı irsaliye ID'leri
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
            f"/{PARASUT_COMPANY_ID}/sales_invoices",
            params={"filter[payment_status]": "overdue,not_paid", "page[size]": 25, "sort": "-issue_date"}
        )
        faturalar = []
        for f in sonuc.get("data", []):
            attr = f.get("attributes", {})
            aciklama = attr.get("description") or ""
            # İrsaliye ID çıkar
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
        # Pozitif = bize borçlu (tahsil edilecek), Negatif = biz borçluyuz (ödenecek)
        toplam_alacak = sum(c["bakiye"] for c in cariler if c["bakiye"] > 0)
        toplam_odeyecek = sum(abs(c["bakiye"]) for c in cariler if c["bakiye"] < 0)
        return {
            "toplam_cari": len(cariler),
            "toplam_alacak": toplam_alacak,       # Bize borçlu
            "toplam_borclu": toplam_odeyecek,      # Biz borçluyuz
        }
    except Exception as e:
        logger.error(f"Hesap ozeti hatasi: {e}")
        return {}


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
_kasa_hesabi_id = None  # Cache — değişmez

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


def fatura_tahsilat_ekle(fatura_id: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    """Parasut'te belirli bir satis faturasina tahsilat ekler."""
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
                    "description": aciklama or "Tahsilat",
                    "date": bugun,
                    "amount": tutar,
                    "account_id": int(hesap_id),
                }
            }
        }

        try:
            logger.info(f"Tahsilat deneniyor: fatura={fatura_id} tutar={tutar:,.0f}")
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices/{fatura_id}/payments", istek)
        except Exception as e:
            # amount string olarak dene
            istek["data"]["attributes"]["amount"] = str(tutar)
            logger.warning(f"Tahsilat v2 deneniyor: {e}")
            sonuc = parasut_post(f"/{PARASUT_COMPANY_ID}/sales_invoices/{fatura_id}/payments", istek)

        odeme_id = sonuc["data"]["id"]
        logger.info(f"Tahsilat eklendi: fatura={fatura_id} tutar={tutar:,.0f} TL")

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
        logger.error(f"Fatura tahsilat hatasi: {e}")
        return {"basarili": False, "hata": str(e)}


def tahsilat_ekle(cari_adi: str, tutar: float, aciklama: str = None, tarih: str = None) -> dict:
    """Belirli bir carinin acik faturalarina tahsilat ekler. Birden fazla faturaya boler."""
    try:
        cari_id = cari_id_bul(cari_adi)
        if not cari_id:
            return {"basarili": False, "hata": f"'{cari_adi}' adli cari bulunamadi."}

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

            odeme_sonuc = fatura_tahsilat_ekle(
                fatura_id=fatura["id"],
                tutar=odeme_miktari,
                aciklama=aciklama or f"{cari_adi} tahsilat",
                tarih=tarih
            )

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


def tum_bekleyen_irsaliyeleri_getir() -> dict:
    """
    Tüm irsaliyeleri TEK API isteğiyle çeker.
    Cari bazlı ayrı ayrı istek atmak yerine tüm shipment_documents'ı çek,
    sonra contact ID'ye göre grupla.
    Returns: {contact_id: [irsaliye, ...]}
    """
    try:
        sonuc = parasut_get(
            f"/{PARASUT_COMPANY_ID}/shipment_documents",
            params={
                "page[size]": 25,
                "sort": "-issue_date",
                "include": "stock_movements,contact"
            }
        )

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

        gruplu = {}  # contact_id → [irsaliye]
        for i in sonuc.get("data", []):
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

        logger.info(f"Bekleyen irsaliyeler: {sum(len(v) for v in gruplu.values())} toplam, {len(gruplu)} cari")
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