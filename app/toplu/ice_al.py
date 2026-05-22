# ice_al.py — Onaylı toplu satırları sisteme alma (WeighTicket + irsaliye)
#
# Her onaylı satır için:
#   1) Cari resolver ile firma belirle (modüler)
#   2) WeighTicket oluştur (foto yok — kaynak: toplu içe alım)
#   3) irsaliye_olustur(beklet=True) — mevcut pipeline; fiyat sonra toplu faturada
#
# Mükerrer koruması: aynı fiş no aktif statüde varsa atlanır (kontrol.py ile aynı set).

from datetime import datetime
from app.core.database import SessionLocal, WeighTicket, TicketStatus
from app.core.logger import logger
from app.toplu.cari_resolver import resolver_getir
from app.toplu.kontrol import _AKTIF_STATULER


def _tarih_parse(tarih_str):
    if not tarih_str:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(tarih_str).strip(), fmt)
        except ValueError:
            continue
    return None


def _mevcut_aktif_var(db, fis_no, tenant_id) -> bool:
    if not fis_no:
        return False
    q = db.query(WeighTicket).filter(WeighTicket.fis_no == fis_no)
    if tenant_id:
        q = q.filter(WeighTicket.tenant_id == tenant_id)
    return any(t.status in _AKTIF_STATULER for t in q.all())


def toplu_ice_al(satirlar: list, tenant_id: str, secilen_cari: str = None,
                 tarih: str = None, resolver_tipi: str = "tek_cari",
                 ctx_extra: dict = None) -> dict:
    """
    satirlar: [{fis_no, plaka, net_kg, sofor?, tesis?}, ...] — kullanıcının ONAYLADIĞI satırlar.
    Dönüş: {basarili, ozet:{...}, sonuclar:[{fis_no, plaka, durum, irsaliye_id?, hata?}]}
    """
    from app.parasut.irsaliye import irsaliye_olustur  # lazy — Paraşüt katmanı

    resolver = resolver_getir(resolver_tipi)
    ctx = {"secilen_cari": secilen_cari, "tenant_id": tenant_id, **(ctx_extra or {})}
    fis_tarihi = _tarih_parse(tarih) or datetime.now()

    sonuclar = []
    sayac = {"olusturuldu": 0, "atlandi": 0, "hata": 0}

    for s in satirlar:
        fis_no = (s.get("fis_no") or "").strip() or None
        plaka  = (s.get("plaka") or "").strip().upper() or None
        net    = int(s.get("net_kg") or 0)
        kayit  = {"fis_no": fis_no, "plaka": plaka, "net_kg": net}

        cari = resolver.resolve(s, ctx)
        if not cari:
            kayit.update(durum="hata", hata="Cari belirlenemedi")
            sayac["hata"] += 1
            sonuclar.append(kayit)
            continue
        if net <= 0:
            kayit.update(durum="hata", hata="Net ağırlık 0")
            sayac["hata"] += 1
            sonuclar.append(kayit)
            continue

        db = SessionLocal()
        try:
            if _mevcut_aktif_var(db, fis_no, tenant_id):
                kayit.update(durum="atlandi", hata="Bu fiş no zaten sistemde aktif")
                sayac["atlandi"] += 1
                sonuclar.append(kayit)
                continue

            ticket = WeighTicket(
                tenant_id=tenant_id, foto_path=None,
                plaka=plaka, fis_no=fis_no,
                agirlik_kg=net, net_tartim_kg=net, fire_kg=0,
                firma=cari, fis_tarihi=fis_tarihi,
                malzeme="hurda", malzemeler=None,
                ocr_ham_cikti="toplu_ice_alim",
                status=TicketStatus.OCR_TAMAMLANDI,
            )
            db.add(ticket)
            db.commit()
            db.refresh(ticket)
            ticket_id = ticket.id
        except Exception as e:
            db.rollback()
            kayit.update(durum="hata", hata=f"Kayıt hatası: {e}")
            sayac["hata"] += 1
            sonuclar.append(kayit)
            db.close()
            continue
        finally:
            db.close()

        # İrsaliye oluştur (beklet=True → FABRIKA_BEKLENIYOR; fiyat sonra toplu faturada)
        try:
            sonuc = irsaliye_olustur(ticket_id, True, tenant_id)
            if sonuc.get("basarili"):
                kayit.update(durum="olusturuldu", cari=cari,
                             irsaliye_id=str(sonuc.get("parasut_id", "")))
                sayac["olusturuldu"] += 1
            else:
                kayit.update(durum="hata", cari=cari, hata=sonuc.get("hata", "irsaliye hatası"))
                sayac["hata"] += 1
        except Exception as e:
            kayit.update(durum="hata", cari=cari, hata=f"İrsaliye hatası: {e}")
            sayac["hata"] += 1
        sonuclar.append(kayit)

    ozet = {"toplam": len(satirlar), **sayac}
    logger.info(f"Toplu içe alım tamamlandı: {ozet}")
    return {"basarili": sayac["hata"] == 0, "ozet": ozet, "sonuclar": sonuclar}
