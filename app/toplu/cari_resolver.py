# cari_resolver.py — Toplu içe alımda cari (firma) çözümleme — MODÜLER
#
# Toplu listede genelde sadece "Tesis" (fabrika) var, tedarikçi/cari firma yok.
# Her satırın hangi cariye işleneceği iş akışına göre DEĞİŞİR. Bu yüzden strateji
# deseni: yeni kural gerektiğinde sadece yeni bir Resolver sınıfı + RESOLVERS'a kayıt.
#
# ŞİMDİLİK: TekCariResolver (parti için elle seçilen tek cari).
# GELECEK örnekleri (henüz yok): TesisCariResolver (Tesis adından cari eşle),
#   PlakaCariResolver (plaka→cari kayıtlı eşleme tablosu), KolonCariResolver (Excel'de Firma kolonu).

from app.core.logger import logger


class CariResolver:
    """Bir toplu-fiş satırını bir cari adına çözen strateji arayüzü."""
    ad = "base"

    def resolve(self, satir: dict, ctx: dict):
        """satir: {fis_no, plaka, sofor, net_kg, tesis}; ctx: {secilen_cari, tenant_id, ...}
        Dönüş: cari_adi (str) veya None."""
        raise NotImplementedError


class TekCariResolver(CariResolver):
    """Tüm parti için tek, elle seçilen cari. ctx['secilen_cari'] kullanılır."""
    ad = "tek_cari"

    def resolve(self, satir: dict, ctx: dict):
        return (ctx.get("secilen_cari") or "").strip() or None


class TesisCariResolver(CariResolver):
    """Satırdaki 'Tesis' alanını cari adı olarak kullanır (Tesis = müşteri/fabrika).
    İleride Tesis→cari eşleme tablosuyla genişletilebilir."""
    ad = "tesis"

    def resolve(self, satir: dict, ctx: dict):
        eslem = ctx.get("tesis_cari_eslem") or {}
        tesis = (satir.get("tesis") or "").strip()
        if not tesis:
            return None
        return eslem.get(tesis, tesis) or None


# Kayıtlı stratejiler — yeni kural eklemek = buraya bir satır.
RESOLVERS = {
    TekCariResolver.ad: TekCariResolver(),
    TesisCariResolver.ad: TesisCariResolver(),
}

VARSAYILAN = TekCariResolver.ad


def resolver_getir(tip: str = None) -> CariResolver:
    r = RESOLVERS.get((tip or VARSAYILAN))
    if r is None:
        logger.warning(f"Bilinmeyen cari resolver '{tip}', varsayılana düşülüyor ({VARSAYILAN})")
        r = RESOLVERS[VARSAYILAN]
    return r
