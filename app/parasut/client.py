# app/parasut/client.py
# ---------------------------------------------------------------------
# BACKWARD-COMPAT SHIM
# ---------------------------------------------------------------------
# Bu dosya eskiden 2600+ satırlık monolit Paraşüt istemcisiydi.
# İçerik şu modüllere bölündü:
#
#   _state.py         — per-tenant state (token cache, cari cache, locks)
#   _http.py          — rate limit + parasut_get/post/delete/put/patch
#   token.py          — _tenant_credentials, get_tenant_config, get_token
#   cari.py           — cari listesi/cache/CRUD/fuzzy id_bul
#   tahsilat.py       — kasa + tahsilat/ödeme/transfer
#   satis.py          — sales_invoices iptal/güncelle/arşiv + ortak helper'lar
#   alis.py           — purchase_bills oluştur/iptal/güncelle/arşiv/detay/ödeme
#   fatura_sorgu.py   — cari_faturalari_getir, fatura_detay_getir, *_universal
#   irsaliye_sorgu.py — bekleyen irsaliyeler + detay
#   rapor.py          — hesap_ozeti, islem_gecmisi, gelir/gider, stok, hesaplar
#   e_fatura.py       — e-fatura/e-arşiv oluştur + mükellef + trackable_job
#   async_wrappers.py — tüm async_* (asyncio.to_thread)
#
# Eski "from app.parasut.client import X" import'ları çalışmaya devam etsin
# diye burada tüm public API'yi re-export ediyoruz.
#
# YENİ KOD yazıyorsan ilgili modülü doğrudan import et:
#   from app.parasut.token import get_token
#   from app.parasut.cari  import cari_id_bul
# ---------------------------------------------------------------------

# Module-level state (mutate edilebilir — diğer modüller _state.py'den
# referans alıyor, burası sadece backward-compat için yeniden export).
from ._state import (
    _tokens, _token_locks,
    _cari_caches, _cari_cache_times, _cache_locks,
    _kasa_hesabi_ids, _rate_limit_times, _meta_lock,
    CACHE_SURE, MIN_ISTEK_ARASI,
    _resolve_tid, _get_lock,
)

from ._http import (
    _rate_limit_bekle, _istek_yap,
    parasut_get, parasut_post, parasut_delete, parasut_put, parasut_patch,
)

from .token import (
    _tenant_credentials, get_tenant_config,
    _token_al, _token_yenile, get_token,
)

from .cari import (
    _cari_sayfa_cek, _cari_listesi_api,
    cari_cache_yenile, cari_listesi_getir,
    _GEREKSIZ_KELIMELER, _FUZZY_ESIK,
    _normalize_cari_adi, _fuzzy_skor, cari_id_bul,
    cari_detay_getir, cari_olustur, cari_guncelle, cari_tipi_bul,
)

from .tahsilat import (
    _kasa_hesabi_bul,
    fatura_tahsilat_ekle, tahsilat_sil,
    cari_bazli_tahsilat, cari_bazli_odeme,
    acik_faturalar_getir, tahsilat_ekle, para_transferi,
)

from .satis import (
    _sales_faturalari_page, _fatura_dict_olustur,
    _SALES_INVOICE_ATTRIBUTES, _PURCHASE_BILL_ATTRIBUTES,
    _attrs_ve_rels_hazirla,
    satis_faturasi_iptal, satis_faturasi_geri_al,
    satis_faturasi_arsivle, satis_faturasi_arsivden_cikar,
    satis_faturasi_guncelle,
)

from .alis import (
    _purchase_faturalari_page,
    alis_faturasi_olustur, alis_faturasi_basit_olustur,
    alis_faturasi_iptal, alis_faturasi_geri_al,
    alis_faturasi_arsivle, alis_faturasi_arsivden_cikar,
    alis_faturasi_guncelle, alis_faturasi_detay_getir,
    alıs_faturasi_odeme_ekle, son_alis_faturalari_getir,
)

from .fatura_sorgu import (
    cari_faturalari_getir, fatura_detay_getir, cari_irsaliyeleri_getir,
    fatura_detay_universal, fatura_iptal_universal, fatura_guncelle_universal,
)

from .irsaliye_sorgu import (
    tum_bekleyen_irsaliyeleri_getir,
    cari_bekleyen_irsaliyeleri_getir,
    irsaliye_detay_getir,
)

from .rapor import (
    hesap_ozeti_getir, islem_gecmisi_getir, hesap_listesi_getir,
    stok_hareketleri_getir, gelir_gider_raporu_getir,
)

from .e_fatura import (
    e_fatura_mukellef_kontrol,
    trackable_job_durum, trackable_job_bekle,
    e_arsiv_olustur, e_fatura_olustur,
    e_arsiv_detay, e_fatura_detay,
    fatura_resmilestir_otomatik,
)

# Async wrapper'lar — async_* fonksiyonlar
from .async_wrappers import *  # noqa: F401,F403
