# listener.py — Gelen Kutusu Dinleyici
#
# GELİŞTİRME: Mailhog HTTP API kullanır
# CANLI: Gerçek IMAP sunucusu kullanır
#
# NEDEN HTTP API?
# Mailhog IMAP desteklemiyor.
# Ama kendi HTTP API'si var: localhost:8025/api/v2/messages
# Bu API tüm mailleri JSON olarak döndürüyor.
# IMAP'tan daha basit, geliştirme için mükemmel.

import httpx
import time
from app.core.config import ENV
from app.core.database import SessionLocal, SystemLog
from app.core.logger import logger
from app.mail.parser import fabrika_cevabi_isle, ref_kodu_bul


# ── FONKSİYON 1: MAİLHOG'DAN MAİLLERİ AL ───────────────────────────────
def mailhog_mailleri_al() -> list:
    """
    Mailhog HTTP API'sinden tüm mailleri alır.

    NEDEN HTTP API?
    Mailhog IMAP desteklemiyor.
    localhost:8025/api/v2/messages → tüm mailleri JSON döndürür.

    CANLI ORTAMDA NE OLACAK?
    Gerçek mail sunucusu IMAP destekler.
    O zaman bu fonksiyon IMAP bağlantısına çevrilir.
    Sadece bu fonksiyon değişir, geri kalan kod aynı kalır.
    """
    try:
        response = httpx.get(
            "http://localhost:8025/api/v2/messages",
            params={"limit": 50}  # Son 50 maili al
        )

        if response.status_code == 200:
            veri = response.json()
            return veri.get("items", [])
        else:
            logger.error(f"Mailhog API hatası: {response.status_code}")
            return []

    except Exception as e:
        logger.error(f"Mailhog bağlantı hatası: {e}")
        return []


# ── FONKSİYON 2: MAİL İÇERİĞİNİ ÇIKAR ──────────────────────────────────
def mail_icerik_cikar(mail: dict) -> tuple:
    """
    Mailhog JSON formatından konu ve gövdeyi çıkarır.

    Mailhog JSON yapısı:
    {
        "Content": {
            "Headers": {
                "Subject": ["İrsaliye Onayı [Ref: HZ-0001]"],
                "From": ["hamza@hamzaai.com"]
            },
            "Body": "Mail gövdesi..."
        }
    }

    NEDEN TUPLE DÖNDÜRÜYOR?
    İki değer döndürmemiz lazım: konu ve gövde.
    Tuple → (konu, gövde) şeklinde ikisini birlikte döndürürüz.
    """
    try:
        icerik = mail.get("Content", {})
        headers = icerik.get("Headers", {})

        # Konu başlığı liste olarak geliyor — ilk elemanı al
        konu_liste = headers.get("Subject", [""])
        konu = konu_liste[0] if konu_liste else ""
        # Encoding varsa çöz
        from email.header import decode_header as dh
        parcalar = dh(konu)
        konu_temiz = ""
        for parca, enc in parcalar:
            if isinstance(parca, bytes):
                konu_temiz += parca.decode(enc or "utf-8", errors="ignore")
            else:
                konu_temiz += str(parca)
        konu = konu_temiz

        # Gövde
        govde = icerik.get("Body", "")

        return konu, govde

    except Exception as e:
        logger.error(f"Mail içerik çıkarma hatası: {e}")
        return "", ""


# ── FONKSİYON 3: ANA DÖNGÜ ──────────────────────────────────────────────
def gelen_kutu_dinle(bekleme_suresi: int = 60):
    """
    Ana dinleme döngüsü — 7/24 çalışır.

    AKIŞ:
    1. Mailhog'dan mailleri al
    2. Her mailde [Ref: HZ-XXXX] var mı bak
    3. Varsa parser.py'e gönder
    4. İşlenmiş mailleri tekrar işleme
    5. Bekle, tekrar başa dön

    islenen_mailler SET'i:
    Her mailin benzersiz ID'sini tutar.
    Aynı mail iki kez işlenmez.
    Set kullanıyoruz çünkü "var mı?" kontrolü çok hızlı.
    """
    logger.info(f"📬 Mail dinleyici başlatıldı (her {bekleme_suresi}sn kontrol)")

    islenen_mailler = set()

    while True:
        try:
            db = SessionLocal()
            mailler = mailhog_mailleri_al()

            if not mailler:
                logger.debug("Inbox boş veya bağlanamadı")
                db.close()
                time.sleep(bekleme_suresi)
                continue

            yeni_sayisi = 0

            for mail in mailler:
                # Mail ID'si
                mail_id = mail.get("ID", "")

                if not mail_id:
                    continue

                # Daha önce işledik mi?
                if mail_id in islenen_mailler:
                    continue

                # İçeriği çıkar
                konu, govde = mail_icerik_cikar(mail)

                # Gönderen kim?
                headers = mail.get("Content", {}).get("Headers", {})
                gonderen = headers.get("From", [""])[0]

                logger.debug(f"Mail kontrol: '{konu[:50]}' | Gönderen: {gonderen}")
                # Sadece fabrikadan gelen mailleri işle
                # Hamza'nın kendi gönderdiği mailler atlanır
                if "hamzaai.com" in gonderen:
                    islenen_mailler.add(mail_id)
                    continue

                # [Ref: HZ-XXXX] var mı?
                ref = ref_kodu_bul(konu, govde)

                if ref:
                    # Fabrika teyit maili!
                    logger.info(f"📨 Fabrika teyit maili yakalandı: [{ref}]")

                    sonuc = fabrika_cevabi_isle(
                        mail_konusu=konu,
                        mail_govdesi=govde,
                        muhasebeci_mail="muhasebe@hamzaai.com",
                        db=db
                    )

                    logger.info(f"✅ İşlendi: [{ref}] → {sonuc.get('karar', 'BILINMIYOR')}")
                    yeni_sayisi += 1

                else:
                    logger.debug(f"Ref kodu yok, atlandı: '{konu[:30]}'")

                # Her durumda işlendi olarak işaretle
                islenen_mailler.add(mail_id)

            if yeni_sayisi > 0:
                logger.info(f"✅ {yeni_sayisi} yeni fabrika maili işlendi")

            db.close()

        except Exception as e:
            logger.error(f"Listener hatası: {e}")

        logger.debug(f"⏳ {bekleme_suresi}sn bekleniyor...")
        time.sleep(bekleme_suresi)