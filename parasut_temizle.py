import requests
import os
import time
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv("PARASUT_CLIENT_ID")
CLIENT_SECRET = os.getenv("PARASUT_CLIENT_SECRET")
USERNAME      = os.getenv("PARASUT_USERNAME")
PASSWORD      = os.getenv("PARASUT_PASSWORD")
COMPANY_ID    = os.getenv("PARASUT_COMPANY_ID")
BASE_URL      = "https://api.parasut.com/v4"


def token_al():
    r = requests.post("https://api.parasut.com/oauth/token", data={
        "grant_type": "password",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username": USERNAME,
        "password": PASSWORD,
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"
    })
    r.raise_for_status()
    return r.json()["access_token"]


def liste_al(token, endpoint, tip):
    headers = {"Authorization": f"Bearer {token}"}
    kayitlar = []
    page = 1
    while True:
        r = requests.get(f"{BASE_URL}/{COMPANY_ID}/{endpoint}?page[size]=25&page[number]={page}", headers=headers)
        if r.status_code == 429:
            print(f"  Rate limit, 10sn bekleniyor...")
            time.sleep(10)
            continue
        if r.status_code != 200:
            print(f"Liste alınamadı ({tip}): {r.status_code}")
            break
        veri = r.json().get("data", [])
        if not veri:
            break
        kayitlar.extend(veri)
        print(f"  {tip}: {len(kayitlar)} kayıt alındı...")
        if len(veri) < 25:
            break
        page += 1
        time.sleep(1)
    return kayitlar


def sil(token, endpoint, kayit_id, tip):
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        r = requests.delete(f"{BASE_URL}/{COMPANY_ID}/{endpoint}/{kayit_id}", headers=headers)
        if r.status_code == 429:
            bekleme = 7
            print(f"  Rate limit, {bekleme}sn bekleniyor...")
            time.sleep(bekleme)
            continue
        if r.status_code in [200, 204]:
            print(f"  ✅ Silindi: {tip} #{kayit_id}")
            return True
        else:
            print(f"  ❌ Silinemedi: {tip} #{kayit_id} — {r.status_code}")
            return False


def main():
    print("🔑 Token alınıyor...")
    token = token_al()
    print("✅ Token alındı\n")

    # Faturalar
    print("📋 Faturalar listeleniyor...")
    faturalar = liste_al(token, "sales_invoices", "Fatura")
    print(f"  Toplam {len(faturalar)} fatura bulundu\n")

    if faturalar:
        onay = input(f"⚠️  {len(faturalar)} fatura silinecek. Onaylıyor musunuz? (evet/hayır): ")
        if onay.lower() == "evet":
            for f in faturalar:
                sil(token, "sales_invoices", f["id"], "Fatura")
                time.sleep(1)  # Her silmeden sonra 1sn bekle
            print(f"\n✅ Faturalar tamamlandı\n")

    time.sleep(5)

    # İrsaliyeler
    print("📋 İrsaliyeler listeleniyor...")
    irsaliyeler = liste_al(token, "shipment_documents", "İrsaliye")
    print(f"  Toplam {len(irsaliyeler)} irsaliye bulundu\n")

    if irsaliyeler:
        onay = input(f"⚠️  {len(irsaliyeler)} irsaliye silinecek. Onaylıyor musunuz? (evet/hayır): ")
        if onay.lower() == "evet":
            for i in irsaliyeler:
                sil(token, "shipment_documents", i["id"], "İrsaliye")
                time.sleep(1)
            print(f"\n✅ İrsaliyeler tamamlandı\n")

    print("🎉 Temizleme tamamlandı!")


if __name__ == "__main__":
    main()