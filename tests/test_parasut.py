import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.parasut.client import get_token, parasut_get
from app.core.config import PARASUT_COMPANY_ID

# Token testi
token = get_token()
print("Token alındı:", token[:20], "...")

# Cari listesi
sonuc = parasut_get(f"/{PARASUT_COMPANY_ID}/contacts?page[size]=5")
cariler = sonuc.get("data", [])
print("Cari sayısı:", len(cariler))

for cari in cariler:
    isim = cari["attributes"]["name"]
    cari_id = cari["id"]
    print(f"  id={cari_id} isim={isim}")