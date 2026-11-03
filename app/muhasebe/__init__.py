# app/muhasebe — Muhasebe sağlayıcı katmanı
#
# Router'lar muhasebe işlemleri için SADECE bu paketi kullanır:
#
#     from app.muhasebe import async_cari_id_bul, async_tahsilat_ekle
#
# Hangi sağlayıcının çalışacağını .env'deki MUHASEBE_SAGLAYICI belirler:
#   yerel   → app.muhasebe.yerel   (Profaix'in kendi veritabanı — varsayılan)
#   parasut → app.parasut.client   (Paraşüt V4 API)
#
# İki sağlayıcı da aynı fonksiyon adlarını ve dönüş formatlarını sağlar.
# Yeni sağlayıcı eklemek (örn. Logo, Mikro): app/muhasebe/<ad>/ paketi oluştur,
# aynı fonksiyonları yaz, aşağıdaki _modul() içine ekle.

import importlib

from app.core.config import MUHASEBE_SAGLAYICI

_MODULLER = {
    "yerel":   "app.muhasebe.yerel",
    "parasut": "app.parasut.client",
}
_ADLAR = {
    "yerel":   "Profaix Yerel Kayıt",
    "parasut": "Paraşüt",
}


def aktif_saglayici() -> str:
    """'yerel' veya 'parasut'."""
    return MUHASEBE_SAGLAYICI


def saglayici_adi() -> str:
    """Kullanıcıya gösterilecek ad (mesajlarda 'X'e kaydedildi' gibi)."""
    return _ADLAR.get(MUHASEBE_SAGLAYICI, MUHASEBE_SAGLAYICI)


def parasut_mu() -> bool:
    return MUHASEBE_SAGLAYICI == "parasut"


def _modul():
    return importlib.import_module(_MODULLER[MUHASEBE_SAGLAYICI])


def __getattr__(ad: str):
    # PEP 562: `from app.muhasebe import async_X` aktif sağlayıcıdan çözülür.
    if ad.startswith("__"):
        raise AttributeError(ad)
    try:
        return getattr(_modul(), ad)
    except AttributeError:
        raise AttributeError(
            f"'{ad}' muhasebe sağlayıcısında ({MUHASEBE_SAGLAYICI}) tanımlı değil"
        ) from None
