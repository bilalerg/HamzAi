# cari_ac.py — Paraşüt'te yeni cari hesap açar
# Çalıştır: python cari_ac.py

import requests
import os
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv("PARASUT_CLIENT_ID")
CLIENT_SECRET = os.getenv("PARASUT_CLIENT_SECRET")
USERNAME      = os.getenv("PARASUT_USERNAME")
PASSWORD      = os.getenv("PARASUT_PASSWORD")
COMPANY_ID    = os.getenv("PARASUT_COMPANY_ID")
BASE_URL      = "https://api.parasut.com/v4"

# ── Açılacak firmalar ──────────────────────────────────────────────
FIRMALAR = [
    {"ad": "Samsun Makine"},
    {"ad": "Kroman"},
    {"ad": "İçtaş Demir Çelik Tersane"},
    {"ad": "Kaptan Demir Çelik"},
]


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


def cari_ac(token, firma_adi):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/vnd.api+json"
    }
    veri = {
        "data": {
            "type": "contacts",
            "attributes": {
                "name": firma_adi,
                "contact_type": "company",
                "account_type": "customer"
            }
        }
    }
    r = requests.post(f"{BASE_URL}/{COMPANY_ID}/contacts", headers=headers, json=veri)
    if r.status_code in [200, 201]:
        contact_id = r.json()["data"]["id"]
        print(f"  ✅ {firma_adi} → ID: {contact_id}")
        return contact_id
    else:
        print(f"  ❌ {firma_adi} → Hata: {r.status_code} {r.text[:100]}")
        return None


def main():
    print("🔑 Token alınıyor...")
    token = token_al()
    print("✅ Token alındı\n")

    print("📋 Cari hesaplar açılıyor...\n")
    sonuclar = {}
    for firma in FIRMALAR:
        contact_id = cari_ac(token, firma["ad"])
        if contact_id:
            sonuclar[firma["ad"]] = contact_id

    print("\n✅ Tamamlandı!")
    print("\n⚠️  Bu ID'leri config.py'e eklemen lazım:")
    for ad, cid in sonuclar.items():
        print(f'  "{ad}": {cid}')


if __name__ == "__main__":
    main()