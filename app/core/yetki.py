# yetki.py — Rol bazlı yetki (RBAC)
#
# Rol kaynağı: profaix.com /users endpoint'inin "role" alanı.
# Değerler: superadmin > owner > operation.  (Local admin: "admin".)
#
# Yetki anahtarları endpoint gruplarına karşılık gelir; main.py middleware'i
# path → yetki eşlemesiyle backend'de zorlar, frontend menüyü buna göre filtreler.

from app.core.logger import logger

# Tüm yetki anahtarları
FIS_YUKLE = "fis_yukle"   # OCR fiş yükleme (saha)
TOPLU     = "toplu"        # toplu Excel/PDF içe alma
FATURA    = "fatura"       # bekleyen irsaliye, fatura kes/iptal/güncelle, e-fatura, alış faturası
TAHSILAT  = "tahsilat"     # tahsilat/ödeme/transfer/kasa
CARI      = "cari"         # cari listele/oluştur/güncelle
RAPOR     = "rapor"        # raporlar / sorgular
KULLANICI = "kullanici_yonetimi"

TUM_YETKILER = [FIS_YUKLE, TOPLU, FATURA, TAHSILAT, CARI, RAPOR, KULLANICI]

# Rol → yetki kümeleri
ROL_YETKI = {
    "superadmin": set(TUM_YETKILER),
    "admin":      set(TUM_YETKILER),                                   # local admin
    "owner":      {FIS_YUKLE, TOPLU, FATURA, TAHSILAT, CARI, RAPOR},   # firma sahibi
    "operation":  {FIS_YUKLE},                                         # saha: sadece fiş yükleme
}


def _coz(rol) -> str:
    """Rolü normalize eder. Boş/eksik → owner (eski token migration grace);
    tanımlı değilse → operation (en kısıtlı, güvenli taraf)."""
    if rol is None or str(rol).strip() == "":
        return "owner"
    r = str(rol).strip().lower()
    if r in ROL_YETKI:
        return r
    logger.warning(f"Bilinmeyen rol '{rol}' → operation (kısıtlı) varsayıldı")
    return "operation"


def yetkiler_getir(rol) -> list:
    """Rolün sahip olduğu yetki anahtarları (sıralı liste) — /api/me için."""
    return sorted(ROL_YETKI[_coz(rol)])


def yetki_var(rol, anahtar: str) -> bool:
    """rol, anahtar yetkisine sahip mi? — middleware enforcement için."""
    return anahtar in ROL_YETKI[_coz(rol)]
