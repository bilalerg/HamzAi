# plaka_kontrol.py — Plaka Kontrol Sistemi
#
# SORUMLULUK:
# Gelen plakanın daha önce görülüp görülmediğini kontrol eder.
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
    # Alerts tablosuna uyarı yazar — WP gelince buradan okunup gönderilecek
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


def plaka_kontrol_et(plaka: str, ticket_id: int, db) -> PlakaSonuc:
    """
    Plakanın durumunu kontrol eder ve gerekli işlemleri yapar.

    ADIMLAR:
    1. vehicles tablosunda plaka var mı? → Yeni mi değil mi?
    2. Yeni ise → vehicles'a ekle + uyarı oluştur + muhasebeciye bildir
    3. Değil ise → bugün daha önce işlem gördü mü?
    4. Duplicate ise → uyarı oluştur + muhasebeciye bildir
    5. Sonucu döndür
    """

    if not plaka:
        logger.warning("Plaka boş geldi, kontrol atlanıyor")
        return PlakaSonuc.NORMAL

    plaka = plaka.upper().strip()

    # ── ADIM 1: Bu plaka daha önce görülmüş mü? ─────────────────────────
    mevcut_arac = db.query(Vehicle).filter(
        Vehicle.plaka == plaka
    ).first()

    # ── ADIM 2: YENİ PLAKA ──────────────────────────────────────────────
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

        # Muhasebeciye WP bildirimi gönder
        muhasebeciye_bildir(f"🆕 Yeni araç sisteme eklendi: {plaka}\nTicket ID: {ticket_id}")

        return PlakaSonuc.YENI

    # ── ADIM 3: BUGÜN DAHA ÖNCE GELDİ Mİ? ──────────────────────────────
    bugun_baslangic = datetime.combine(date.today(), datetime.min.time())

    bugunki_islem = db.query(WeighTicket).filter(
        WeighTicket.plaka == plaka,
        WeighTicket.created_at >= bugun_baslangic,
        WeighTicket.id != ticket_id  # Kendisini sayma!
    ).first()

    # ── ADIM 4: DUPLICATE ───────────────────────────────────────────────
    if bugunki_islem:
        logger.warning(f"⚠️ Duplicate tespit edildi: {plaka} bugün daha önce gelmiş (ticket_id={bugunki_islem.id})")

        uyari_olustur(
            tur="DUPLICATE",
            mesaj=f"Bu araç bugün daha önce geldi: {plaka} (önceki işlem ID: {bugunki_islem.id})",
            ticket_id=ticket_id,
            db=db
        )

        # Muhasebeciye WP bildirimi gönder
        muhasebeciye_bildir(
            f"⚠️ Duplicate fiş tespit edildi!\n"
            f"🚗 Plaka: {plaka}\n"
            f"Bu araç bugün daha önce geldi.\n"
            f"Önceki işlem ID: {bugunki_islem.id}\n"
            f"Yeni ticket ID: {ticket_id}"
        )

        # Status güncelle — muhasebeci onayı bekleniyor
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()
        if ticket:
            ticket.status = TicketStatus.PLAKA_KONTROL_BEKLENIYOR
            db.commit()

        return PlakaSonuc.DUPLICATE

    # ── ADIM 5: NORMAL AKIŞ ─────────────────────────────────────────────
    logger.info(f"✅ Plaka kontrolü geçti: {plaka} — normal akış")
    return PlakaSonuc.NORMAL