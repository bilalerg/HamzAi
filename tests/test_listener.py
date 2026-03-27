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
from app.core.database import SessionLocal, Waybill, TicketStatus

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

    msg['Subject'] = f"Re: İrsaliye Onayı [Ref: {ref_kodu}]"
    govde = "Onaylıyorum, teşekkürler." if onay_mi else "İtiraz ediyoruz, ağırlık yanlış."

    msg.attach(MIMEText(govde, 'plain', 'utf-8'))

    smtp = smtplib.SMTP("localhost", 1025)
    smtp.send_message(msg)
    smtp.quit()
    print(f"\n📤 Fabrika reply attı: [Ref: {ref_kodu}] → {'ONAY' if onay_mi else 'İTİRAZ'}")


# ── DB'DEN REF KODUNU OTOMATİK AL ────────────────────────────────────────
# Her seferinde elle değiştirmek yerine DB'den en güncel bekleyen waybill'i alıyoruz
# FABRIKA_BEKLENIYOR statusundaki ilk waybill'i bul
db = SessionLocal()
waybill = db.query(Waybill).filter(
    Waybill.status == TicketStatus.FABRIKA_BEKLENIYOR
).first()
db.close()

if not waybill:
    print("❌ DB'de FABRIKA_BEKLENIYOR statusunda waybill yok!")
    print("Önce WhatsApp'tan fiş göndererek irsaliye oluştur.")
    sys.exit(1)

ref_kodu = waybill.ref_kodu
print(f"✅ DB'den ref kodu alındı: {ref_kodu}")


# ── 5 saniye sonra fabrika otomatik reply atacak ─────────────────────────
def zamanlanmis_reply():
    print(f"\n⏳ 5 saniye sonra fabrika [{ref_kodu}] için reply atacak...")
    time.sleep(5)
    fabrika_reply_at(ref_kodu, onay_mi=True)


# Arka planda çalıştır
threading.Thread(target=zamanlanmis_reply, daemon=True).start()

# ── Listener başlat ───────────────────────────────────────────────────────
print(f"\n📬 Listener başlatılıyor (5sn'de bir kontrol)...")
print(f"5 saniye sonra fabrika [{ref_kodu}] için onay atacak...")
print("-" * 50)
gelen_kutu_dinle(bekleme_suresi=5)