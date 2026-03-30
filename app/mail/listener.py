# listener.py — Gelen Kutusu Dinleyici
#
# Gmail IMAP ile mailleri dinler.
# İşlenen mail ID'leri DB'ye kaydedilir — restart sonrası tekrar işlenmez.
# SADECE "Re:" ile başlayan mailler işlenir — orijinal mailler atlanır.

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


# ── YARDIMCI: GÜVENLİ DECODE ─────────────────────────────────────────────
def guvenli_decode(payload, charset):
    """
    Bilinmeyen veya geçersiz encoding'leri güvenli şekilde çözer.
    unknown-8bit gibi non-standard encoding'ler için latin-1 kullanır.
    """
    if not charset or charset.lower() in ["unknown-8bit", "unknown", ""]:
        charset = "latin-1"
    try:
        return payload.decode(charset, errors="ignore")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("latin-1", errors="ignore")


# ── FONKSİYON 2: MAİL İÇERİĞİNİ ÇIKAR ──────────────────────────────────
def mail_icerik_cikar(mail_verisi: dict) -> tuple:
    try:
        msg = email.message_from_bytes(mail_verisi["ham"])

        konu_ham = msg.get("Subject", "")
        parcalar = decode_header(konu_ham)
        konu = ""
        for parca, enc in parcalar:
            if isinstance(parca, bytes):
                konu += guvenli_decode(parca, enc)
            else:
                konu += str(parca)

        gonderen = msg.get("From", "")

        govde = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    charset = part.get_content_charset() or "utf-8"
                    govde = guvenli_decode(part.get_payload(decode=True), charset)
                    break
        else:
            charset = msg.get_content_charset() or "utf-8"
            govde = guvenli_decode(msg.get_payload(decode=True), charset)

        return konu, govde, gonderen

    except Exception as e:
        logger.error(f"Mail içerik çıkarma hatası: {e}")
        return "", "", ""


# ── FONKSİYON 3: DB'DEN İŞLENMİŞ MAİLLERİ YÜKLE ────────────────────────
def islenmis_mailleri_yukle(db) -> set:
    try:
        kayitlar = db.query(ProcessedMail).all()
        return {k.mail_id for k in kayitlar}
    except Exception as e:
        logger.error(f"İşlenmiş mailler yüklenemedi: {e}")
        return set()


# ── FONKSİYON 4: MAİL ID'Sİ DB'YE KAYDET ────────────────────────────────
def mail_islendi_kaydet(db, mail_id: str, ref_kodu: str = None):
    try:
        # Zaten varsa ekleme — UniqueViolation önle
        mevcut = db.query(ProcessedMail).filter(
            ProcessedMail.mail_id == mail_id
        ).first()
        if mevcut:
            return
        kayit = ProcessedMail(mail_id=mail_id, ref_kodu=ref_kodu)
        db.add(kayit)
        db.commit()
    except Exception as e:
        logger.error(f"Mail ID kaydedilemedi: {e}")
        db.rollback()


# ── FONKSİYON 5: ANA DÖNGÜ ──────────────────────────────────────────────
def gelen_kutu_dinle(bekleme_suresi: int = 60):
    logger.info(f"📬 Mail dinleyici başlatıldı (her {bekleme_suresi}sn kontrol)")

    while True:
        try:
            db = SessionLocal()

            islenen_mailler = islenmis_mailleri_yukle(db)
            mailler = imap_mailleri_al()

            if not mailler:
                logger.info("Inbox boş veya bağlanamadı")
                db.close()
                time.sleep(bekleme_suresi)
                continue

            yeni_sayisi = 0

            for mail in mailler:
                mail_id = mail["id"]

                if mail_id in islenen_mailler:
                    continue

                konu, govde, gonderen = mail_icerik_cikar(mail)

                logger.debug(f"Mail kontrol: '{konu[:50]}' | Gönderen: {gonderen}")

                # Sadece reply mailleri işle — orijinal mailler atlanır
                if not konu.startswith("Re:") and not konu.startswith("RE:") and not konu.startswith("Ynt:"):
                    logger.debug(f"Reply değil, atlandı: '{konu[:40]}'")
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

                mail_islendi_kaydet(db, mail_id, ref)

            if yeni_sayisi > 0:
                logger.info(f"✅ {yeni_sayisi} yeni fabrika maili işlendi")

            db.close()

        except Exception as e:
            logger.error(f"Listener hatası: {e}")

        logger.debug(f"⏳ {bekleme_suresi}sn bekleniyor...")
        time.sleep(bekleme_suresi)