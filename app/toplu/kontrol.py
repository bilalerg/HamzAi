# kontrol.py — Toplu fiş mutabakatı
#
# Yüklenen liste ile sistemdeki mevcut fişleri Fiş No bazında karşılaştırır:
#   yeni       → sistemde yok, içe alınabilir
#   mevcut     → zaten girilmiş (aynı fiş no, aktif statü) → atla
#   uyusmazlik → fiş no var ama net ağırlık tutmuyor → kullanıcı incelesin

from app.core.database import SessionLocal, WeighTicket, TicketStatus
from app.core.logger import logger

# Mükerrer sayılan aktif statüler (plaka_kontrol ile aynı mantık) —
# OCR_TAMAMLANDI/IPTAL/HATA henüz "girilmiş" sayılmaz.
_AKTIF_STATULER = {
    TicketStatus.IRSALIYE_OLUSTURULUYOR,
    TicketStatus.IRSALIYE_TAMAMLANDI,
    TicketStatus.FABRIKA_BEKLENIYOR,
    TicketStatus.FABRIKA_ITIRAZI,
    TicketStatus.FATURA_KESILIYOR,
    TicketStatus.TAMAMLANDI,
}

# Ağırlık uyuşmazlığı toleransı (kg) — küçük yuvarlamalar uyuşmazlık sayılmasın
_TOLERANS_KG = 50


def mutabakat(satirlar: list, tenant_id: str) -> dict:
    """
    satirlar: parser.dosya_ayristir çıktısı.
    Dönüş: {ozet:{...}, satirlar:[{...,durum,mevcut_net,mevcut_status}]}
    """
    db = SessionLocal()
    try:
        fis_nolar = [s["fis_no"] for s in satirlar if s.get("fis_no")]
        mevcut = {}
        if fis_nolar:
            q = db.query(WeighTicket).filter(WeighTicket.fis_no.in_(fis_nolar))
            if tenant_id:
                q = q.filter(WeighTicket.tenant_id == tenant_id)
            for t in q.all():
                # Aynı fiş no birden fazlaysa aktif olanı tercih et
                onceki = mevcut.get(t.fis_no)
                if onceki is None or (t.status in _AKTIF_STATULER and onceki.status not in _AKTIF_STATULER):
                    mevcut[t.fis_no] = t

        zenginlestirilmis = []
        sayac = {"yeni": 0, "mevcut": 0, "uyusmazlik": 0, "fis_no_yok": 0}
        for s in satirlar:
            fn = s.get("fis_no")
            durum, mevcut_net, mevcut_status = "yeni", None, None
            if not fn:
                durum = "fis_no_yok"  # fiş no'suz satır eşleştirilemez (yine de eklenebilir)
            else:
                t = mevcut.get(fn)
                if t is not None and t.status in _AKTIF_STATULER:
                    mevcut_net = t.net_tartim_kg or t.agirlik_kg
                    mevcut_status = t.status.value if hasattr(t.status, "value") else str(t.status)
                    fark = abs((mevcut_net or 0) - (s.get("net_kg") or 0))
                    durum = "uyusmazlik" if fark > _TOLERANS_KG else "mevcut"
            sayac[durum] = sayac.get(durum, 0) + 1
            zenginlestirilmis.append({**s, "durum": durum,
                                      "mevcut_net": mevcut_net, "mevcut_status": mevcut_status})

        ozet = {
            "toplam": len(satirlar),
            "yeni": sayac.get("yeni", 0),
            "mevcut": sayac.get("mevcut", 0),
            "uyusmazlik": sayac.get("uyusmazlik", 0),
            "fis_no_yok": sayac.get("fis_no_yok", 0),
            "toplam_net_kg": sum(s.get("net_kg") or 0 for s in satirlar),
        }
        logger.info(f"Toplu mutabakat: {ozet}")
        return {"ozet": ozet, "satirlar": zenginlestirilmis}
    finally:
        db.close()
