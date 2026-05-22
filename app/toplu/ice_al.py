# ice_al.py — Onaylı toplu satırları sisteme alma (KONSOLİDE irsaliye)
#
# Akış:
#   1) Her satır → cari resolver ile firma belirle (modüler), WeighTicket oluştur
#      (mükerrer fiş no aktifse atla). Ticket'lar fiş no mutabakatı/izlenebilirlik için.
#   2) Yeni ticket'ları cariye göre GRUPLA → her grup için TEK konsolide irsaliye
#      (tek toplam satır). Böylece 24 araç Paraşüt'te 1 belge olur (panel şişmez).
#   3) Fatura: sonra "bekleyen irsaliyeler → toplu fatura" akışında tek fiyatla tek fatura.

import json
from collections import defaultdict
from datetime import datetime
from app.core.database import SessionLocal, WeighTicket, Waybill, TicketStatus
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
    satirlar: kullanıcının ONAYLADIĞI satırlar [{fis_no, plaka, net_kg, ...}].
    Dönüş: {basarili, ozet, irsaliyeler:[{cari, parasut_id, arac, toplam_kg}], sonuclar:[...]}
    """
    from app.parasut.irsaliye import toplu_irsaliye_olustur  # lazy — Paraşüt katmanı

    resolver = resolver_getir(resolver_tipi)
    ctx = {"secilen_cari": secilen_cari, "tenant_id": tenant_id, **(ctx_extra or {})}
    fis_tarihi = _tarih_parse(tarih) or datetime.now()

    sonuclar = []
    sayac = {"eklenen": 0, "atlandi": 0, "hata": 0}
    # cari → [ticket bilgileri]
    gruplar = defaultdict(list)

    # ── 1. Ticket'ları oluştur (mükerrer atla) ──
    for s in satirlar:
        fis_no = (s.get("fis_no") or "").strip() or None
        plaka  = (s.get("plaka") or "").strip().upper() or None
        net    = int(s.get("net_kg") or 0)
        kayit  = {"fis_no": fis_no, "plaka": plaka, "net_kg": net}

        cari = resolver.resolve(s, ctx)
        if not cari:
            kayit.update(durum="hata", hata="Cari belirlenemedi"); sayac["hata"] += 1
            sonuclar.append(kayit); continue
        if net <= 0:
            kayit.update(durum="hata", hata="Net ağırlık 0"); sayac["hata"] += 1
            sonuclar.append(kayit); continue

        db = SessionLocal()
        try:
            if _mevcut_aktif_var(db, fis_no, tenant_id):
                kayit.update(durum="atlandi", hata="Bu fiş no zaten sistemde aktif")
                sayac["atlandi"] += 1; sonuclar.append(kayit); continue
            ticket = WeighTicket(
                tenant_id=tenant_id, foto_path=None,
                plaka=plaka, fis_no=fis_no,
                agirlik_kg=net, net_tartim_kg=net, fire_kg=0,
                firma=cari, fis_tarihi=fis_tarihi,
                malzeme="hurda", malzemeler=None,
                ocr_ham_cikti="toplu_ice_alim",
                status=TicketStatus.OCR_TAMAMLANDI,
            )
            db.add(ticket); db.commit(); db.refresh(ticket)
            gruplar[cari].append({"id": ticket.id, "net": net, "fis_no": fis_no, "plaka": plaka})
            kayit.update(durum="eklendi", cari=cari); sayac["eklenen"] += 1
            sonuclar.append(kayit)
        except Exception as e:
            db.rollback()
            kayit.update(durum="hata", hata=f"Kayıt hatası: {e}"); sayac["hata"] += 1
            sonuclar.append(kayit)
        finally:
            db.close()

    # ── 2. Cari grubu başına TEK konsolide irsaliye ──
    irsaliyeler = []
    for cari, grup in gruplar.items():
        toplam = sum(t["net"] for t in grup)
        aciklama = f"Toplu alım — {cari} — {len(grup)} araç — {fis_tarihi.strftime('%d.%m.%Y')}"
        sonuc = toplu_irsaliye_olustur(cari, toplam, fis_tarihi, aciklama, tenant_id)

        if sonuc.get("basarili"):
            pid = sonuc["parasut_id"]
            db = SessionLocal()
            try:
                wb = Waybill(
                    irsaliye_no=str(pid), parasut_irsaliye_id=str(pid),
                    status=TicketStatus.FABRIKA_BEKLENIYOR,
                    ticket_id=grup[0]["id"], tenant_id=tenant_id,
                    malzemeler_json=json.dumps(
                        [{"plaka": t["plaka"], "fis_no": t["fis_no"], "net_kg": t["net"]} for t in grup],
                        ensure_ascii=False),
                )
                db.add(wb)
                for t in grup:
                    tk = db.query(WeighTicket).filter(WeighTicket.id == t["id"]).first()
                    if tk:
                        tk.status = TicketStatus.FABRIKA_BEKLENIYOR
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Toplu waybill/status hatası: {e}")
            finally:
                db.close()
            irsaliyeler.append({"cari": cari, "parasut_id": pid,
                                "arac": len(grup), "toplam_kg": toplam})
        else:
            # İrsaliye başarısız → bu grubun ticket'larını HATA yap, sayaçları düzelt
            db = SessionLocal()
            try:
                for t in grup:
                    tk = db.query(WeighTicket).filter(WeighTicket.id == t["id"]).first()
                    if tk:
                        tk.status = TicketStatus.HATA
                        tk.hata_mesaji = sonuc.get("hata", "irsaliye hatası")
                db.commit()
            finally:
                db.close()
            sayac["eklenen"] -= len(grup)
            sayac["hata"] += len(grup)
            for kayit in sonuclar:
                if kayit.get("durum") == "eklendi" and kayit.get("cari") == cari:
                    kayit.update(durum="hata", hata=sonuc.get("hata", "irsaliye hatası"))

    ozet = {
        "toplam_satir": len(satirlar),
        "eklenen": sayac["eklenen"],
        "atlandi": sayac["atlandi"],
        "hata": sayac["hata"],
        "irsaliye": len(irsaliyeler),
    }
    logger.info(f"Toplu içe alım (konsolide): {ozet}")
    return {"basarili": sayac["hata"] == 0, "ozet": ozet,
            "irsaliyeler": irsaliyeler, "sonuclar": sonuclar}
