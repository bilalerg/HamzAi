import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ocr.reader import fis_oku, fis_db_kaydet
from app.ocr.plaka_kontrol import plaka_kontrol_et, PlakaSonuc
from app.core.config import validate_config
from app.core.database import SessionLocal
from app.core.logger import logger

validate_config()

fis_klasoru = "tests/fis_fotolari"
fotograflar = sorted([f for f in os.listdir(fis_klasoru) if f.endswith('.jpeg') or f.endswith('.jpg')])

# İlk fişi test et
fotograf = os.path.join(fis_klasoru, fotograflar[0])
print(f"\n📸 Test: {fotograflar[0]}")
print("-" * 50)

db = SessionLocal()
try:
    # 1. OCR oku
    sonuc = fis_oku(fotograf)
    print(f"🚗 Plaka:    {sonuc.plaka}")
    print(f"⚖️  Ağırlık: {sonuc.net_agirlik_kg} kg")

    # 2. DB'ye kaydet
    ticket = fis_db_kaydet(sonuc, fotograf, db)
    print(f"✅ DB kaydedildi: ticket_id={ticket.id}")

    # 3. Plaka kontrolü
    print(f"\n🔍 Plaka kontrolü yapılıyor: {sonuc.plaka}")
    plaka_sonuc = plaka_kontrol_et(sonuc.plaka, ticket.id, db)

    if plaka_sonuc == PlakaSonuc.YENI:
        print(f"🆕 Sonuç: YENİ PLAKA — vehicles'a eklendi, uyarı oluşturuldu")
    elif plaka_sonuc == PlakaSonuc.DUPLICATE:
        print(f"⚠️  Sonuç: DUPLICATE — bugün daha önce gelmiş, uyarı oluşturuldu")
    else:
        print(f"✅ Sonuç: NORMAL — devam et")

finally:
    db.close()