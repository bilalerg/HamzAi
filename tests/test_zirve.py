import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import validate_config
from app.core.logger import logger
from app.zirve.otomasyon import irsaliye_olustur

validate_config()

print("\n🤖 Form doldurma testi başlıyor...")
print("5 saniye içinde Zirve irsaliye formuna git!")
print("-" * 50)

import time
time.sleep(5)

sonuc = irsaliye_olustur(
    ticket_id=3,
    cari_adi="TEST FABRIKA",
    stok_kodu="HURDA",
    miktar=29400,
    tarih="20.03.2026",
    sifre="1234"
)

print(f"\n{'✅ Başarılı!' if sonuc['basarili'] else '❌ Başarısız!'}")
print(f"İrsaliye No: {sonuc.get('irsaliye_no')}")
print(f"Ref Kodu:    {sonuc.get('ref_kodu')}")
if sonuc.get('hata'):
    print(f"Hata:        {sonuc.get('hata')}")