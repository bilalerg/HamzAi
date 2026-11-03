# app/parasut/tahsilat.py
# ---------------------------------------------------------------------
# TAHSILAT / ODEME / PARA TRANSFERI
# - Kasa hesabi bulma (cache: _kasa_hesabi_ids)
# - Fatura uzerine tahsilat (fatura_tahsilat_ekle)
# - Cari hesabi bazli tahsilat (contact_credit_transactions)
# - Cari hesabi bazli odeme   (contact_debit_transactions)
# - Tahsilat silme (transaction_id ile)
# - Acik faturalar listesi (tahsilat akisinda kullanilir)
# - Acik faturaya otomatik dagitim (tahsilat_ekle)
# - Hesaplar arasi para transferi
#
# !! Paraskut V4 doc YANILTICI:
#   contact_credit_transactions = UI'da "Tahsilat" (alacak)
#   contact_debit_transactions  = UI'da "Odeme"   (borc)
# Asla degistirilmemeli; kullanici testleriyle dogrulandi.
# ---------------------------------------------------------------------

import re

from app.core.logger import logger

from ._state import _kasa_hesabi_ids, _resolve_tid
from .token import _tenant_credentials, get_tenant_config
from ._http import parasut_get, parasut_post, parasut_delete
from .cari import cari_id_bul, cari_listesi_getir, cari_tipi_bul, cari_cache_yenile


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
    Fatura olmadan doğrudan cari hesabına TAHSİLAT ekler.

    GERÇEK DAVRANIŞ (KAPTAN DEMİR testiyle doğrulandı 19.05.2026):
      contact_CREDIT_transactions → Paraşüt UI'da 'Tahsilat' olarak yazılır,
        bakiyeyi negatife götürür.
      contact_debit_transactions → Paraşüt UI'da 'Ödeme' olarak yazılır,
        bakiyeyi pozitife götürür.

    Paraşüt API V4 dokümanındaki 'Tahsilat: debit_transactions' etiketi
    YANILTICI — gerçek Paraşüt UI davranışı ters yönde. Profaix'ten
    'Tahsilat ekle' tıklandığında Paraşüt'te de 'Tahsilat' olarak yazılması
    için contact_credit_transactions kullanıyoruz.
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
        logger.info(f"Cari bazlı tahsilat (UI:credit): cari_id={cari_id} tutar={tutar:,.0f} TL tx={tx_id} (tenant={tid})")
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
    Fatura olmadan doğrudan cari hesabına ÖDEME ekler.

    GERÇEK DAVRANIŞ (KAPTAN DEMİR + Kroman testiyle doğrulandı):
      contact_DEBIT_transactions → Paraşüt UI'da 'Ödeme' olarak yazılır,
        bakiyeyi pozitife götürür.
      contact_credit_transactions → Paraşüt UI'da 'Tahsilat' olarak yazılır,
        bakiyeyi negatife götürür.

    Paraşüt API V4 dokümanındaki 'Ödeme: credit_transactions' etiketi
    YANILTICI — gerçek Paraşüt UI davranışı ters yönde. Profaix'ten
    'Ödeme ekle' tıklandığında Paraşüt'te de 'Ödeme' olarak yazılması
    için contact_debit_transactions kullanıyoruz.
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
        logger.info(f"Cari bazlı ödeme (UI:debit): cari_id={cari_id} tutar={tutar:,.0f} TL tx={tx_id} (tenant={tid})")
        try:
            cari_cache_yenile(tenant_id=tid)
        except Exception:
            pass
        return {"basarili": True, "id": tx_id, "cari_id": cari_id, "tutar": tutar, "tarih": bugun}
    except Exception as e:
        logger.error(f"Cari bazlı ödeme hatası: {e}")
        return {"basarili": False, "hata": str(e)}




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

