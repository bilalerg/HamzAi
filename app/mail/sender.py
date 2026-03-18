# sender.py — Mail Gönderme Modülü
#
# SORUMLULUK:
# 1. Fabrikaya irsaliye teyit maili gönderir ([Ref: HZ-XXXX] kodu ile)
# 2. Muhasebeciye uyarı maili gönderir
# 3. Patron'a bildirim maili gönderir
#
# NEDEN SMTP?
# IMAP → mail okumak için
# SMTP → mail göndermek için
# Mailhog hem IMAP hem SMTP destekler → geliştirmede gerçek mail gitmiyor
# Canlıda sadece .env'deki MAIL_HOST değişecek

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os
from app.core.config import MAIL_HOST, MAIL_PORT, MAIL_FROM
from app.core.logger import logger


# ── YARDIMCI: SMTP BAĞLANTISI ───────────────────────────────────────────
def smtp_baglan():
    """
    SMTP sunucusuna bağlanır.

    NEDEN CONTEXT MANAGER DEĞİL?
    smtplib.SMTP() with bloğu ile kullanılabilir ama
    her fonksiyonda ayrı bağlantı açıp kapatmak daha temiz.
    Mailhog için authentication gerekmez — production'da gerekir.
    """
    try:
        smtp = smtplib.SMTP(MAIL_HOST, MAIL_PORT)
        logger.debug(f"SMTP bağlantısı kuruldu: {MAIL_HOST}:{MAIL_PORT}")
        return smtp
    except Exception as e:
        logger.error(f"SMTP bağlantı hatası: {e}")
        raise


# ── FONKSİYON 1: FABRİKAYA İRSALİYE MAİLİ ──────────────────────────────
def fabrikaya_irsaliye_gonder(
        fabrika_mail: str,
        waybill_id: int,
        plaka: str,
        agirlik_kg: int,
        tarih: str,
        irsaliye_no: str = None
) -> str:
    """
    Fabrikaya irsaliye teyit maili gönderir.

    NEDEN REF KODU?
    Fabrika reply atarken konu değiştirebilir, yeni mail açabilir.
    [Ref: HZ-0001] kodu her durumda mail içinde kalır.
    Biz gelen mailde bu kodu Regex ile arayıp ilgili irsaliyeyi buluruz.

    Döndürür: ref_kodu (DB'ye kaydetmek için)
    """

    # Ref kodu oluştur: waybill_id=1 → "HZ-0001"
    ref_kodu = f"HZ-{waybill_id:04d}"

    # Mail oluştur
    # MIMEMultipart → hem metin hem ek içerebilen mail formatı
    msg = MIMEMultipart()
    msg['From'] = MAIL_FROM
    msg['To'] = fabrika_mail
    msg['Subject'] = f"İrsaliye Onayı [Ref: {ref_kodu}]"

    # Mail gövdesi
    # NEDEN F-STRING?
    # Değişkenleri direkt string içine gömmek için.
    # Okunması kolay, hata riski az.
    govde = f"""Sayın Yetkili,

Aşağıdaki sevkiyat için irsaliye oluşturulmuştur.
Lütfen bilgileri kontrol edip onaylayınız.

━━━━━━━━━━━━━━━━━━━━━━━━
Araç Plakası  : {plaka}
Net Ağırlık   : {agirlik_kg:,} kg
Tarih         : {tarih}
İrsaliye No   : {irsaliye_no or 'Oluşturuluyor'}
Referans      : {ref_kodu}
━━━━━━━━━━━━━━━━━━━━━━━━

Onaylamak için bu maili YANITLAYINIZ.
İtiraz durumunda lütfen sebebini belirtiniz.

HamzaAI Otomatik Bildirim Sistemi
"""

    msg.attach(MIMEText(govde, 'plain', 'utf-8'))

    # Gönder
    try:
        smtp = smtp_baglan()
        smtp.send_message(msg)
        smtp.quit()
        logger.info(f"✅ Fabrika maili gönderildi: {fabrika_mail} [Ref: {ref_kodu}]")
        return ref_kodu
    except Exception as e:
        logger.error(f"Fabrika maili gönderilemedi: {e}")
        raise


# ── FONKSİYON 2: MUHASEBECİYE UYARI MAİLİ ──────────────────────────────
def muhasebeciye_uyari_gonder(
        muhasebeci_mail: str,
        konu: str,
        mesaj: str
) -> bool:
    """
    Muhasebeciye uyarı maili gönderir.

    Kullanım alanları:
    - Yeni plaka tespit edildi
    - Duplicate uyarısı
    - OCR uyuşmazlığı
    - Fabrika itirazı
    """
    msg = MIMEMultipart()
    msg['From'] = MAIL_FROM
    msg['To'] = muhasebeci_mail
    msg['Subject'] = f"⚠️ HamzaAI Uyarı: {konu}"

    govde = f"""HamzaAI Uyarı Bildirimi

{mesaj}

━━━━━━━━━━━━━━━━━━━━━━━━
Bu mesaj HamzaAI tarafından otomatik gönderilmiştir.
"""

    msg.attach(MIMEText(govde, 'plain', 'utf-8'))

    try:
        smtp = smtp_baglan()
        smtp.send_message(msg)
        smtp.quit()
        logger.info(f"✅ Muhasebeci uyarısı gönderildi: {konu}")
        return True
    except Exception as e:
        logger.error(f"Muhasebeci maili gönderilemedi: {e}")
        return False


# ── FONKSİYON 3: PATRON BİLDİRİM MAİLİ ─────────────────────────────────
def patrona_bildirim_gonder(
        patron_mail: str,
        fatura_no: str,
        plaka: str,
        tutar: float = None
) -> bool:
    """
    Fatura kesilince patrona bildirim gönderir.
    Faz 4'te Paraşüt entegrasyonu tamamlanınca çağrılacak.
    """
    msg = MIMEMultipart()
    msg['From'] = MAIL_FROM
    msg['To'] = patron_mail
    msg['Subject'] = f"✅ Fatura Kesildi: {fatura_no}"

    govde = f"""Fatura Bildirimi

Aşağıdaki fatura başarıyla kesilmiştir.

━━━━━━━━━━━━━━━━━━━━━━━━
Fatura No   : {fatura_no}
Araç Plaka  : {plaka}
Tutar       : {f'{tutar:,.2f} TL' if tutar else 'Belirtilmedi'}
━━━━━━━━━━━━━━━━━━━━━━━━

HamzaAI Otomatik Bildirim Sistemi
"""

    msg.attach(MIMEText(govde, 'plain', 'utf-8'))

    try:
        smtp = smtp_baglan()
        smtp.send_message(msg)
        smtp.quit()
        logger.info(f"✅ Patron bildirimi gönderildi: {fatura_no}")
        return True
    except Exception as e:
        logger.error(f"Patron maili gönderilemedi: {e}")
        return False