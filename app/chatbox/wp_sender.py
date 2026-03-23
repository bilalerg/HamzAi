# wp_sender.py — WhatsApp mesaj gönderme

from twilio.rest import Client
from app.core.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM
from app.core.logger import logger


def wp_gonder(alici: str, mesaj: str) -> bool:
    # Twilio üzerinden WhatsApp mesajı gönderir
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=alici,
            body=mesaj
        )
        logger.info(f"✅ WP gönderildi: {alici} → {message.sid}")
        return True
    except Exception as e:
        logger.error(f"WP gönderim hatası: {e}")
        return False


def tirciya_bildir(telefon: str, mesaj: str) -> bool:
    # Tırçıya bildirim gönderir
    alici = f"whatsapp:{telefon}" if not telefon.startswith("whatsapp:") else telefon
    return wp_gonder(alici, mesaj)


def muhasebeciye_bildir(mesaj: str) -> bool:
    # Muhasebeciye bildirim gönderir
    from app.core.config import MUHASEBECI_WHATSAPP
    if not MUHASEBECI_WHATSAPP:
        logger.warning("MUHASEBECI_WHATSAPP tanımlı değil")
        return False
    return wp_gonder(MUHASEBECI_WHATSAPP, mesaj)


def patrona_bildir(mesaj: str) -> bool:
    # Patrona bildirim gönderir
    from app.core.config import PATRON_WHATSAPP
    if not PATRON_WHATSAPP:
        logger.warning("PATRON_WHATSAPP tanımlı değil")
        return False
    return wp_gonder(PATRON_WHATSAPP, mesaj)