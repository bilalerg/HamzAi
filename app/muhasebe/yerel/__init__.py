# app/muhasebe/yerel — Paraşüt'süz yerel muhasebe sağlayıcısı
#
# servis.py'deki senkron fonksiyonları dışa açar ve Paraşüt client'ındaki gibi
# her biri için async_* sürümünü (asyncio.to_thread) üretir.

import asyncio
import functools

from app.muhasebe.yerel import servis as _servis
from app.muhasebe.yerel.servis import *  # noqa: F401,F403
from app.muhasebe.yerel.servis import alıs_faturasi_odeme_ekle, SAGLAYICI_ADI  # noqa: F401

_SENKRON = [
    "cari_listesi_getir", "cari_cache_yenile", "cari_id_bul", "cari_detay_getir",
    "cari_olustur", "cari_guncelle", "cari_tipi_bul",
    "parasut_post", "parasut_delete", "parasut_get", "parasut_put", "parasut_patch",
    "tum_bekleyen_irsaliyeleri_getir", "cari_bekleyen_irsaliyeleri_getir", "irsaliye_detay_getir",
    "cari_faturalari_getir", "fatura_detay_getir", "alis_faturasi_detay_getir",
    "fatura_detay_universal", "fatura_iptal_universal", "fatura_guncelle_universal",
    "satis_faturasi_iptal", "alis_faturasi_iptal",
    "alis_faturasi_olustur", "alis_faturasi_basit_olustur", "son_alis_faturalari_getir",
    "fatura_tahsilat_ekle", "tahsilat_ekle", "tahsilat_sil",
    "alıs_faturasi_odeme_ekle", "alis_faturasi_odeme_ekle",
    "cari_bazli_tahsilat", "cari_bazli_odeme", "acik_faturalar_getir",
    "hesap_listesi_getir", "para_transferi",
    "hesap_ozeti_getir", "islem_gecmisi_getir", "stok_hareketleri_getir", "gelir_gider_raporu_getir",
    "e_fatura_mukellef_kontrol", "fatura_resmilestir_otomatik",
]


def _asyncle(fn):
    @functools.wraps(fn)
    async def _sarmal(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return _sarmal


for _ad in _SENKRON:
    globals()["async_" + _ad] = _asyncle(getattr(_servis, _ad))

get_tenant_config = _servis.get_tenant_config
