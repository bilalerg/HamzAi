# app/parasut/satis.py
# ---------------------------------------------------------------------
# SATIS FATURALARI (sales_invoices) + ortak fatura helper'lari
# - _sales_faturalari_page (page-by-page cekme)
# - _fatura_dict_olustur   (sales+purchase yanitini standart dict'e cevirir)
# - _SALES_INVOICE_ATTRIBUTES / _PURCHASE_BILL_ATTRIBUTES (guncellenebilir alanlar)
# - _attrs_ve_rels_hazirla (PUT body uretici)
# - satis_faturasi_iptal / geri_al / arsivle / arsivden_cikar
# - satis_faturasi_guncelle
#
# Not: cari_faturalari_getir + fatura_detay_getir + universal wrapper'lar
# fatura_sorgu.py icinde — onlar hem satis hem alis'i import eder.
# ---------------------------------------------------------------------

from app.core.logger import logger

from ._state import _resolve_tid
from .token import _tenant_credentials, get_tenant_config
from ._http import parasut_get, parasut_post, parasut_put, parasut_patch


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


