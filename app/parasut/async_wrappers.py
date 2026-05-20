# app/parasut/async_wrappers.py
# ---------------------------------------------------------------------
# ASYNC WRAPPER'LAR — tum sync fonksiyonlar icin asyncio.to_thread().
# FastAPI async endpoint'lerinden bunlar kullanilmali (event loop bloklanmaz).
# ---------------------------------------------------------------------

import asyncio

# Wildcard import: tum public fonksiyonlar bu namespace'ten erisilebilir.
# Her modul kendi public API'sini export eder; client.py de buradan import eder.
from ._http         import (parasut_get, parasut_post, parasut_delete, parasut_put, parasut_patch)
from .token         import get_token, get_tenant_config
from .cari          import (
    cari_listesi_getir, cari_cache_yenile, cari_id_bul, cari_detay_getir,
    cari_olustur, cari_guncelle, cari_tipi_bul,
)
from .tahsilat      import (
    fatura_tahsilat_ekle, tahsilat_sil, cari_bazli_tahsilat,
    cari_bazli_odeme, tahsilat_ekle, para_transferi, acik_faturalar_getir,
)
from .satis         import (
    satis_faturasi_iptal, satis_faturasi_geri_al,
    satis_faturasi_arsivle, satis_faturasi_arsivden_cikar, satis_faturasi_guncelle,
)
from .alis          import (
    alis_faturasi_olustur, alis_faturasi_basit_olustur,
    alis_faturasi_iptal, alis_faturasi_geri_al,
    alis_faturasi_arsivle, alis_faturasi_arsivden_cikar,
    alis_faturasi_guncelle, alis_faturasi_detay_getir,
    alis_faturasi_odeme_ekle, son_alis_faturalari_getir,
)
from .fatura_sorgu  import (
    cari_faturalari_getir, fatura_detay_getir,
    fatura_detay_universal, fatura_iptal_universal, fatura_guncelle_universal,
)
from .irsaliye_sorgu import (
    tum_bekleyen_irsaliyeleri_getir, cari_bekleyen_irsaliyeleri_getir,
    irsaliye_detay_getir,
)
from .rapor         import (
    hesap_ozeti_getir, islem_gecmisi_getir, hesap_listesi_getir,
    stok_hareketleri_getir, gelir_gider_raporu_getir,
)
from .e_fatura      import (
    e_fatura_mukellef_kontrol, trackable_job_durum, trackable_job_bekle,
    e_arsiv_olustur, e_fatura_olustur, e_arsiv_detay, e_fatura_detay,
    fatura_resmilestir_otomatik,
)
# alıs_faturasi_odeme_ekle Turkce ı icerdiginden alias gerekiyor
from .alis import alıs_faturasi_odeme_ekle

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
