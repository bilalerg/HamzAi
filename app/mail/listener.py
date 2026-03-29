# listener.py — Gelen Kutusu Dinleyici
#
# Gmail IMAP ile mailleri dinler.
# İşlenen mail ID'leri DB'ye kaydedilir — restart sonrası tekrar işlenmez.

import imaplib
import email
import time
from email.header import decode_header
from app.core.config import IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD, MAIL_USER
from app.core.database import SessionLocal, ProcessedMail
from app.core.logger import logger
from app.mail.parser import fabrika_cevabi_isle, ref_kodu_bul


# ── FONKSİYON 1: IMAP'TAN MAİLLERİ AL ──────────────────────────────────
def imap_mailleri_al() -> list:
    """
    Gmail IMAP üzerinden SADECE OKUNMAMIŞ (UNSEEN) mailleri alır.
    fetch yapınca mail otomatik "Okundu" olur.
    """
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASSWORD)
        mail.select("INBOX")

        durum, mesaj_idleri = mail.search(None, "UNSEEN")

        if durum != "OK" or not mesaj_idleri[0]:
            mail.logout()
            return []

        id_listesi = mesaj_idleri[0].split()
        son_idler = id_listesi[-20:] if len(id_listesi) > 20 else id_listesi

        mailler = []
        for mid in son_idler:
            durum, veri = mail.fetch(mid, "(RFC822)")
            if durum == "OK":
                mailler.append({
                    "id": mid.decode(),
                    "ham": veri[0][1]
                })

        mail.logout()
        return mailler

    except Exception as e:
        logger.error(f"IMAP bağlantı hatası: {e}")
        return []


# ── FONKSİYON 2: MAİL İÇERİĞİNİ ÇIKAR ──────────────────────────────────
def mail_icerik_cikar(mail_verisi: dict) -> tuple:
    """
    Ham IMAP mail verisinden konu, gövde ve gönderen çıkarır.
    Döndürür: (konu, govde, gonderen)
    """
    try:
        msg = email.message_from_bytes(mail_verisi["ham"])

        konu_ham = msg.get("Subject", "")
        parcalar = decode_header(konu_ham)
        konu = ""
        for parca, enc in parcalar:
            if isinstance(parca, bytes):
                konu += parca.decode(enc or "utf-8", errors="ignore")
            else:
                konu += str(parca)

        gonderen = msg.get("From", "")

        govde = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    charset = part.get_content_charset() or "utf-8"
                    govde = part.get_payload(decode=True).decode(charset, errors="ignore")
                    break
        else:
            charset = msg.get_content_charset() or "utf-8"
            govde = msg.get_payload(decode=True).decode(charset, errors="ignore")

        return konu, govde, gonderen

    except Exception as e:
        logger.error(f"Mail içerik çıkarma hatası: {e}")
        return "", "", ""


# ── FONKSİYON 3: DB'DEN İŞLENMİŞ MAİLLERİ YÜKlE ────────────────────────
def islenmis_mailleri_yukle(db) -> set:
    """
    DB'deki işlenmiş mail ID'lerini yükler.
    Restart sonrası eski maillerin tekrar işlenmesini önler.
    """
    try:
        kayitlar = db.query(ProcessedMail).all()
        return {k.mail_id for k in kayitlar}
    except Exception as e:
        logger.error(f"İşlenmiş mailler yüklenemedi: {e}")
        return set()


# ── FONKSİYON 4: MAİL ID'Sİ DB'YE KAYDET ────────────────────────────────
def mail_islendi_kaydet(db, mail_id: str, ref_kodu: str = None):
    """
    İşlenen mail ID'sini DB'ye kaydeder.
    Bir sonraki çalışmada veya restart sonrası tekrar işlenmez.
    """
    try:
        kayit = ProcessedMail(mail_id=mail_id, ref_kodu=ref_kodu)
        db.add(kayit)
        db.commit()
    except Exception as e:
        logger.error(f"Mail ID kaydedilemedi: {e}")
        db.rollback()


# ── FONKSİYON 5: ANA DÖNGÜ ──────────────────────────────────────────────
def gelen_kutu_dinle(bekleme_suresi: int = 60):
    """
    Ana dinleme döngüsü — 7/24 çalışır.

    AKIŞ:
    1. DB'den daha önce işlenmiş mail ID'lerini yükle
    2. Gmail IMAP'tan yeni mailleri al
    3. Her mailde [Ref: HZ-XXXX] var mı bak
    4. Kendi gönderdiğimiz mailleri atla
    5. Fabrika reply'ını yakala → parser.py'e gönder
    6. İşlenen mail ID'sini DB'ye kaydet (restart güvenli)
    7. Bekle, tekrar başa dön
    """
    logger.info(f"📬 Mail dinleyici başlatıldı (her {bekleme_suresi}sn kontrol)")

    while True:
        try:
            db = SessionLocal()

            # DB'den işlenmiş mail ID'lerini yükle — restart güvenli
            islenen_mailler = islenmis_mailleri_yukle(db)

            mailler = imap_mailleri_al()

            if not mailler:
                logger.debug("Inbox boş veya bağlanamadı")
                db.close()
                time.sleep(bekleme_suresi)
                continue

            yeni_sayisi = 0

            for mail in mailler:
                mail_id = mail["id"]

                # DB'de kayıtlı mı? — restart sonrası da korumalı
                if mail_id in islenen_mailler:
                    continue

                konu, govde, gonderen = mail_icerik_cikar(mail)

                logger.debug(f"Mail kontrol: '{konu[:50]}' | Gönderen: {gonderen}")

                # Kendi gönderdiğimiz orijinal mailleri atla, reply'ları işle
                if MAIL_USER and MAIL_USER in gonderen and not konu.startswith("Re:"):
                    mail_islendi_kaydet(db, mail_id)
                    continue

                ref = ref_kodu_bul(konu, govde)

                if ref:
                    logger.info(f"📨 Fabrika teyit maili yakalandı: [{ref}]")

                    sonuc = fabrika_cevabi_isle(
                        mail_konusu=konu,
                        mail_govdesi=govde,
                        muhasebeci_mail=MAIL_USER,
                        db=db
                    )

                    logger.info(f"✅ İşlendi: [{ref}] → {sonuc.get('karar', 'BILINMIYOR')}")
                    yeni_sayisi += 1
                else:
                    logger.debug(f"Ref kodu yok, atlandı: '{konu[:30]}'")

                # Her durumda DB'ye kaydet
                mail_islendi_kaydet(db, mail_id, ref)

            if yeni_sayisi > 0:
                logger.info(f"✅ {yeni_sayisi} yeni fabrika maili işlendi")

            db.close()

        except Exception as e:
            logger.error(f"Listener hatası: {e}")

        logger.debug(f"⏳ {bekleme_suresi}sn bekleniyor...")
        time.sleep(bekleme_suresi)