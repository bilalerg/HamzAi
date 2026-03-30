# plaka_kontrol.py — Fiş ve Plaka Kontrol Sistemi
#
# SORUMLULUK:
# 1. Fiş no'ya göre duplicate kontrolü yapar (aynı fiş iki kez işlenmez)
# 2. Yeni plaka tespiti yapar — muhasebeciye bildirim gönderir
# 3 sonuç döndürür: YENİ, NORMAL, DUPLICATE

from datetime import datetime, date
from enum import Enum
from app.core.database import Vehicle, WeighTicket, Alert, TicketStatus
from app.core.logger import logger
from app.chatbox.wp_sender import muhasebeciye_bildir


class PlakaSonuc(Enum):
    YENI = "YENI"
    NORMAL = "NORMAL"
    DUPLICATE = "DUPLICATE"


def uyari_olustur(tur: str, mesaj: str, ticket_id: int, db) -> Alert:
    alert = Alert(
        tur=tur,
        mesaj=mesaj,
        ticket_id=ticket_id,
        gonderildi_mi=0
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    logger.info(f"🔔 Uyarı oluşturuldu: tur={tur} ticket_id={ticket_id}")
    return alert


def plaka_kontrol_et(plaka: str, ticket_id: int, db, fis_no: str = None) -> PlakaSonuc:
    """
    Fiş no'ya göre duplicate kontrolü yapar, plaka yeniyse bildirir.

    ADIMLAR:
    1. Fiş no varsa → daha önce işlenmiş mi kontrol et (DUPLICATE)
    2. Plaka yeniyse → vehicles'a ekle + muhasebeciye bildir
    3. Normal akış
    """

    if not plaka:
        logger.warning("Plaka boş geldi, kontrol atlanıyor")
        return PlakaSonuc.NORMAL

    plaka = plaka.upper().strip()

    # ── ADIM 1: FİŞ NO DUPLICATE KONTROLÜ ──────────────────────────────
    # Fiş no her fişe özgündür — aynı fiş no tekrar gelirse duplicate
    if fis_no:
        fis_no = str(fis_no).strip()
        mevcut_fis = db.query(WeighTicket).filter(
            WeighTicket.fis_no == fis_no,
            WeighTicket.id != ticket_id  # Kendisini sayma
        ).first()

        if mevcut_fis:
            logger.warning(f"⚠️ Duplicate fiş no: {fis_no} (önceki ticket_id={mevcut_fis.id})")

            uyari_olustur(
                tur="DUPLICATE",
                mesaj=f"Aynı fiş numarası tekrar geldi: {fis_no} (önceki işlem ID: {mevcut_fis.id})",
                ticket_id=ticket_id,
                db=db
            )

            muhasebeciye_bildir(
                f"⚠️ Duplicate fiş tespit edildi!\n"
                f"📋 Fiş No: {fis_no}\n"
                f"🚗 Plaka: {plaka}\n"
                f"Bu fiş daha önce işlendi!\n"
                f"Önceki ticket ID: {mevcut_fis.id}\n"
                f"Yeni ticket ID: {ticket_id}"
            )

            ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()
            if ticket:
                ticket.status = TicketStatus.PLAKA_KONTROL_BEKLENIYOR
                db.commit()

            return PlakaSonuc.DUPLICATE
    else:
        logger.warning(f"Fiş no okunamadı, plaka bazlı kontrol yapılıyor: {plaka}")

    # ── ADIM 2: YENİ PLAKA KONTROLÜ ─────────────────────────────────────
    mevcut_arac = db.query(Vehicle).filter(
        Vehicle.plaka == plaka
    ).first()

    if not mevcut_arac:
        logger.info(f"🆕 Yeni plaka tespit edildi: {plaka}")

        yeni_arac = Vehicle(plaka=plaka, aktif=1)
        db.add(yeni_arac)
        db.commit()
        db.refresh(yeni_arac)

        uyari_olustur(
            tur="YENI_PLAKA",
            mesaj=f"Yeni araç kaydedildi: {plaka}",
            ticket_id=ticket_id,
            db=db
        )

        muhasebeciye_bildir(f"🆕 Yeni araç sisteme eklendi: {plaka}\nTicket ID: {ticket_id}")

        return PlakaSonuc.YENI

    # ── ADIM 3: NORMAL AKIŞ ─────────────────────────────────────────────
    logger.info(f"✅ Fiş kontrolü geçti: plaka={plaka} fis_no={fis_no} — normal akış")
    return PlakaSonuc.NORMAL