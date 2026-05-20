# app/parasut/fatura_sorgu.py
# ---------------------------------------------------------------------
# UNIVERSAL FATURA SORGU + WRAPPER
# ---------------------------------------------------------------------

from app.core.logger import logger

from ._state import _resolve_tid
from .token import _tenant_credentials, get_tenant_config
from ._http import parasut_get
from .cari import cari_listesi_getir, cari_id_bul
from .satis import (
    _sales_faturalari_page, _fatura_dict_olustur,
    satis_faturasi_guncelle, satis_faturasi_iptal,
)
from .alis import (
    _purchase_faturalari_page,
    alis_faturasi_detay_getir, alis_faturasi_guncelle, alis_faturasi_iptal,
)


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
        # 404 "Record was not found" → büyük ihtimalle fatura purchase_bill,
        # frontend tip=supplier ile yeniden deneyecek. Gürültü yapma, sessizce dön.
        msg = str(e)
        if "404" in msg or "Record was not found" in msg or "not_found" in msg.lower():
            logger.info(f"fatura_detay_getir: sales_invoices'da yok (purchase_bill olabilir): {fatura_id}")
        else:
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


