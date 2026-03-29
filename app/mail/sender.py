# sender.py — Mail Gönderme Modülü
#
# SORUMLULUK:
# 1. Fabrikaya irsaliye teyit maili gönderir ([Ref: HZ-XXXX] kodu ile)
# 2. Muhasebeciye uyarı maili gönderir
# 3. Patron'a bildirim maili gönderir
#
# NEDEN RESEND?
# Render ücretsiz planda SMTP portları bloklu.
# Resend HTTP API kullanır — Render'da sorunsuz çalışır.
# Ücretsiz planda ayda 3000 mail hakkı var.

import resend
import os
from app.core.logger import logger

# Resend API key — .env'den alınır
resend.api_key = os.getenv("RESEND_API_KEY")

# Resend ücretsiz planda sadece onboarding@resend.dev adresinden gönderilebilir
# Kendi domain'ini bağlarsan MAIL_FROM kullanılır
MAIL_FROM = "onboarding@resend.dev"


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
    Döndürür: ref_kodu (DB'ye kaydetmek için)
    """
    ref_kodu = f"HZ-{waybill_id:04d}"

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

Profaix Otomatik Bildirim Sistemi
"""

    try:
        params = {
            "from": MAIL_FROM,
            "to": [fabrika_mail],
            "subject": f"İrsaliye Onayı [Ref: {ref_kodu}]",
            "text": govde,
        }
        resend.Emails.send(params)
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
    """
    govde = f"""Profaix Uyarı Bildirimi

{mesaj}

━━━━━━━━━━━━━━━━━━━━━━━━
Bu mesaj Profaix tarafından otomatik gönderilmiştir.
"""

    try:
        params = {
            "from": MAIL_FROM,
            "to": [muhasebeci_mail],
            "subject": f"⚠️ Profaix Uyarı: {konu}",
            "text": govde,
        }
        resend.Emails.send(params)
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
    """
    govde = f"""Fatura Bildirimi

Aşağıdaki fatura başarıyla kesilmiştir.

━━━━━━━━━━━━━━━━━━━━━━━━
Fatura No   : {fatura_no}
Araç Plaka  : {plaka}
Tutar       : {f'{tutar:,.2f} TL' if tutar else 'Belirtilmedi'}
━━━━━━━━━━━━━━━━━━━━━━━━

Profaix Otomatik Bildirim Sistemi
"""

    try:
        params = {
            "from": MAIL_FROM,
            "to": [patron_mail],
            "subject": f"✅ Fatura Kesildi: {fatura_no}",
            "text": govde,
        }
        resend.Emails.send(params)
        logger.info(f"✅ Patron bildirimi gönderildi: {fatura_no}")
        return True
    except Exception as e:
        logger.error(f"Patron maili gönderilemedi: {e}")
        return False