# app/parasut/alis.py
# ---------------------------------------------------------------------
# ALIS FATURALARI (purchase_bills)
# - _purchase_faturalari_page (page-by-page cekme)
# - alis_faturasi_olustur / _basit_olustur
# - alis_faturasi_iptal / geri_al / arsivle / arsivden_cikar
# - alis_faturasi_guncelle
# - son_alis_faturalari_getir
# - alis_faturasi_odeme_ekle (purchase bill'e payment iliskilendir)
# - alis_faturasi_detay_getir
# ---------------------------------------------------------------------

from app.core.logger import logger

from ._state import _resolve_tid, _cari_caches
from .token import _tenant_credentials, get_tenant_config
from ._http import parasut_get, parasut_post, parasut_put, parasut_patch, parasut_delete
from .cari import cari_id_bul
from .tahsilat import _kasa_hesabi_bul
from app.core.config import PARASUT_HURDA_PRODUCT_ID
from .satis import _attrs_ve_rels_hazirla, _PURCHASE_BILL_ATTRIBUTES, _fatura_dict_olustur


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




# ───────────────────────────────────────────────────────────────────
# ASCII ALIAS — orijinal fonksiyon adi Turkce "i" icerdiginden eski
# kodda hem alis_faturasi_odeme_ekle (ASCII) hem de alıs_... (Turkce)
# kullaniliyor. Asagidaki alias geriye uyumluluk icin.
# ───────────────────────────────────────────────────────────────────
alis_faturasi_odeme_ekle = alıs_faturasi_odeme_ekle
