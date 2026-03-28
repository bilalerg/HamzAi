# listener.py — Gelen Kutusu Dinleyici
#
# Mailhog HTTP API'den Gmail IMAP'a geçildi.
# IMAP → mail okumak için standart protokol.
# Gmail IMAP: imap.gmail.com:993 (SSL)
# Uygulama şifresi ile bağlanıyoruz — normal Gmail şifresi çalışmaz.

import imaplib
import email
import time
from email.header import decode_header
from app.core.config import IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD, MAIL_USER
from app.core.database import SessionLocal
from app.core.logger import logger
from app.mail.parser import fabrika_cevabi_isle, ref_kodu_bul


# ── FONKSİYON 1: IMAP'TAN MAİLLERİ AL ──────────────────────────────────
def imap_mailleri_al() -> list:
    """
    Gmail IMAP üzerinden SADECE OKUNMAMIŞ (UNSEEN) mailleri alır.
    Bir mail (RFC822) ile çekildiğinde otomatik olarak "Okundu" statüsüne geçer.
    """
    try:
        # SSL ile Gmail IMAP'a bağlan
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASSWORD)
        mail.select("INBOX")

        # SADECE OKUNMAMIŞ MAİLLERİ FİLTRELE
        durum, mesaj_idleri = mail.search(None, "UNSEEN")

        if durum != "OK" or not mesaj_idleri[0]:
            mail.logout()
            return []

        id_listesi = mesaj_idleri[0].split()

        # Çok fazla okunmamış biriktiyse sadece son 20'yi al
        son_idler = id_listesi[-20:] if len(id_listesi) > 20 else id_listesi

        mailler = []
        for mid in son_idler:
            # Bu satır çalıştığı an mail Gmail'de "Okundu" olarak işaretlenir
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

    NEDEN RFC822 FORMATI?
    IMAP mailleri RFC822 standardında gelir.
    Python'un email kütüphanesi bunu parse eder.
    Konu encoding'i (UTF-8, ISO-8859-9 vs) otomatik çözülür.

    Döndürür: (konu, govde, gonderen)
    """
    try:
        msg = email.message_from_bytes(mail_verisi["ham"])

        # Konu — encoding çöz
        konu_ham = msg.get("Subject", "")
        parcalar = decode_header(konu_ham)
        konu = ""
        for parca, enc in parcalar:
            if isinstance(parca, bytes):
                konu += parca.decode(enc or "utf-8", errors="ignore")
            else:
                konu += str(parca)

        # Gönderen
        gonderen = msg.get("From", "")

        # Gövde — multipart veya düz metin
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


# ── FONKSİYON 3: ANA DÖNGÜ ──────────────────────────────────────────────
def gelen_kutu_dinle(bekleme_suresi: int = 60):
    """
    Ana dinleme döngüsü — 7/24 çalışır.

    AKIŞ:
    1. Gmail IMAP'tan mailleri al
    2. Her mailde [Ref: HZ-XXXX] var mı bak
    3. Kendi gönderdiğimiz mailleri atla
    4. Fabrika reply'ını yakala → parser.py'e gönder
    5. İşlenmiş mailleri tekrar işleme (set ile takip)
    6. Bekle, tekrar başa dön
    """
    logger.info(f"📬 Mail dinleyici başlatıldı (her {bekleme_suresi}sn kontrol)")

    islenen_mailler = set()
    islenen_refler = set() # 🚀 YENİ: Mükerrer fatura kesimini önlemek için hafıza

    while True:
        try:
            db = SessionLocal()
            mailler = imap_mailleri_al()

            if not mailler:
                logger.debug("Inbox boş veya bağlanamadı")
                db.close()
                time.sleep(bekleme_suresi)
                continue

            yeni_sayisi = 0

            for mail in mailler:
                mail_id = mail["id"]

                # Daha önce işledik mi?
                if mail_id in islenen_mailler:
                    continue

                # İçeriği çıkar
                konu, govde, gonderen = mail_icerik_cikar(mail)

                logger.debug(f"Mail kontrol: '{konu[:50]}' | Gönderen: {gonderen}")

                # Kendi gönderdiğimiz mailleri atla
                # MAIL_USER bizim Gmail adresimiz — biz gönderdikse işleme
                if MAIL_USER and MAIL_USER in gonderen and not konu.startswith("Re:"):
                    islenen_mailler.add(mail_id)
                    continue

                # [Ref: HZ-XXXX] var mı?
                ref = ref_kodu_bul(konu, govde)

                if ref:
                    # 🚀 YENİ KONTROL: Bu Ref kodu bu oturumda zaten faturalandırıldı mı?
                    if ref in islenen_refler:
                        logger.debug(f"⚠️ [{ref}] zaten bu oturumda işlendi, mükerrer mail atlanıyor.")
                        islenen_mailler.add(mail_id)
                        continue

                    logger.info(f"📨 Fabrika teyit maili yakalandı: [{ref}]")

                    sonuc = fabrika_cevabi_isle(
                        mail_konusu=konu,
                        mail_govdesi=govde,
                        muhasebeci_mail=MAIL_USER,
                        db=db
                    )

                    logger.info(f"✅ İşlendi: [{ref}] → {sonuc.get('karar', 'BILINMIYOR')}")
                    yeni_sayisi += 1
                    islenen_refler.add(ref)  # 🚀 YENİ: Başarıyla işlendi, hafızaya al
                else:
                    logger.debug(f"Ref kodu yok, atlandı: '{konu[:30]}'")

                islenen_mailler.add(mail_id)

            if yeni_sayisi > 0:
                logger.info(f"✅ {yeni_sayisi} yeni fabrika maili işlendi")

            db.close()

        except Exception as e:
            logger.error(f"Listener hatası: {e}")

        logger.debug(f"⏳ {bekleme_suresi}sn bekleniyor...")
        time.sleep(bekleme_suresi)