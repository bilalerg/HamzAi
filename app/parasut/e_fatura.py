# app/parasut/e_fatura.py
# ---------------------------------------------------------------------
# E-FATURA / E-ARSIV RESMILESTIRME
# - e_fatura_mukellef_kontrol (VKN ile e-fatura mukellef sorgusu)
# - trackable_job_durum / trackable_job_bekle
# - e_arsiv_olustur / e_fatura_olustur
# - e_arsiv_detay   / e_fatura_detay
# - fatura_resmilestir_otomatik (sales invoice'i e-arsiv veya e-fatura'ya cevirir)
# ---------------------------------------------------------------------

import time

from app.core.logger import logger

from ._state import _resolve_tid
from .token import _tenant_credentials, get_tenant_config
from ._http import parasut_get, parasut_post


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


