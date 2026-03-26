import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
import time

from app.core.config import validate_config
from app.core.logger import logger
from app.mail.listener import gelen_kutu_dinle

validate_config()


# ── YARDIMCI: FABRİKA GİBİ REPLY AT ────────────────────────────────────
def fabrika_reply_at(ref_kodu: str, onay_mi: bool = True):
    """
    Fabrikayı simüle eder — Mailhog'a reply atar.
    Gerçek hayatta fabrika bunu elle yapacak.
    Test için biz Python ile yapıyoruz.
    """
    msg = MIMEMultipart()
    msg['From'] = "fabrika@test.com"
    msg['To'] = "hamza@hamzaai.com"

    if onay_mi:
        msg['Subject'] = f"Re: İrsaliye Onayı [Ref: {ref_kodu}]"
        govde = "Onaylıyorum, teşekkürler."
    else:
        msg['Subject'] = f"Re: İrsaliye Onayı [Ref: {ref_kodu}]"
        govde = "İtiraz ediyoruz, ağırlık yanlış."

    msg.attach(MIMEText(govde, 'plain', 'utf-8'))

    smtp = smtplib.SMTP("localhost", 1025)
    smtp.send_message(msg)
    smtp.quit()
    print(f"\n📤 Fabrika reply attı: [Ref: {ref_kodu}] → {'ONAY' if onay_mi else 'İTİRAZ'}")


# ── 5 saniye sonra fabrika otomatik reply atacak ─────────────────────────
def zamanlanmis_reply():
    print("\n⏳ 5 saniye sonra fabrika reply atacak...")
    time.sleep(5)
    fabrika_reply_at("HZ-0011", onay_mi=True)  # Gerçek waybill ref kodu


# Arka planda çalıştır
threading.Thread(target=zamanlanmis_reply, daemon=True).start()

# ── Listener başlat ───────────────────────────────────────────────────────
print("\n📬 Listener başlatılıyor (5sn'de bir kontrol)...")
print("5 saniye sonra fabrika HZ-0011 için onay atacak...")
print("-" * 50)
gelen_kutu_dinle(bekleme_suresi=5)