"""
parasut_temizle.py — Paraşüt Temizleme Scripti
===============================================
Bu script şunları yapar:
1. Tüm eski SATIŞ faturalarını (sales_invoices) siler
2. Tüm eski irsaliyeleri (shipment_documents) siler
3. Cari hesapları müşteri → tedarikçi olarak günceller

KULLANIM:
    python parasut_temizle.py

UYARI: Bu işlem GERİ ALINAMAZ. Sadece test verisini temizlemek için kullan.
"""

import os
import sys
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

# .env yükle
secret_env = Path("/etc/secrets/.env")
if secret_env.exists():
    load_dotenv(secret_env)
else:
    load_dotenv()

CLIENT_ID     = os.getenv("PARASUT_CLIENT_ID")
CLIENT_SECRET = os.getenv("PARASUT_CLIENT_SECRET")
USERNAME      = os.getenv("PARASUT_USERNAME")
PASSWORD      = os.getenv("PARASUT_PASSWORD")
COMPANY_ID    = os.getenv("PARASUT_COMPANY_ID")
BASE_URL      = "https://api.parasut.com/v4"

# ── TOKEN ─────────────────────────────────────────────────────────────
def token_al() -> str:
    print("🔑 Paraşüt token alınıyor...")
    res = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type":    "password",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username":      USERNAME,
            "password":      PASSWORD,
            "redirect_uri":  "urn:ietf:wg:oauth:2.0:oob"
        }
    )
    res.raise_for_status()
    token = res.json()["access_token"]
    print("✅ Token alındı")
    return token

# ── YARDIMCI ──────────────────────────────────────────────────────────
def get(token: str, path: str, params: dict = None) -> dict:
    time.sleep(0.4)  # Rate limit koruması
    res = requests.get(
        f"{BASE_URL}/{COMPANY_ID}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {}
    )
    res.raise_for_status()
    return res.json()

def delete(token: str, path: str) -> bool:
    time.sleep(0.4)
    res = requests.delete(
        f"{BASE_URL}/{COMPANY_ID}{path}",
        headers={"Authorization": f"Bearer {token}"}
    )
    return res.status_code in (200, 204)

def patch(token: str, path: str, data: dict) -> bool:
    time.sleep(0.4)
    res = requests.patch(
        f"{BASE_URL}/{COMPANY_ID}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=data
    )
    return res.status_code in (200, 204)

# ── 1. SATIŞ FATURALARINI SİL ─────────────────────────────────────────
def satis_faturalarini_sil(token: str):
    print("\n📋 Satış faturaları siliniyor...")
    silinen = 0
    sayfa = 1

    while True:
        try:
            sonuc = get(token, "/sales_invoices", {
                "page[size]": 25,
                "page[number]": sayfa,
                "sort": "-issue_date"
            })
        except Exception as e:
            print(f"  ⚠️ Sayfa {sayfa} okunamadı: {e}")
            break

        faturalar = sonuc.get("data", [])
        if not faturalar:
            break

        for f in faturalar:
            fid = f["id"]
            aciklama = f.get("attributes", {}).get("description", "-")
            try:
                if delete(token, f"/sales_invoices/{fid}"):
                    silinen += 1
                    print(f"  ✅ Silindi: {fid} — {aciklama[:50]}")
                else:
                    print(f"  ❌ Silinemedi: {fid}")
            except Exception as e:
                print(f"  ❌ Hata ({fid}): {e}")

        if len(faturalar) < 25:
            break
        sayfa += 1

    print(f"  Toplam silinen satış faturası: {silinen}")
    return silinen

# ── 2. İRSALİYELERİ SİL ──────────────────────────────────────────────
def irsaliyeleri_sil(token: str):
    print("\n📦 İrsaliyeler siliniyor...")
    silinen = 0
    sayfa = 1

    while True:
        try:
            sonuc = get(token, "/shipment_documents", {
                "page[size]": 25,
                "page[number]": sayfa,
                "sort": "-issue_date"
            })
        except Exception as e:
            print(f"  ⚠️ Sayfa {sayfa} okunamadı: {e}")
            break

        irsaliyeler = sonuc.get("data", [])
        if not irsaliyeler:
            break

        for irs in irsaliyeler:
            iid = irs["id"]
            aciklama = irs.get("attributes", {}).get("description", "-")
            try:
                if delete(token, f"/shipment_documents/{iid}"):
                    silinen += 1
                    print(f"  ✅ Silindi: {iid} — {aciklama[:50]}")
                else:
                    print(f"  ❌ Silinemedi: {iid}")
            except Exception as e:
                print(f"  ❌ Hata ({iid}): {e}")

        if len(irsaliyeler) < 25:
            break
        sayfa += 1

    print(f"  Toplam silinen irsaliye: {silinen}")
    return silinen

# ── 3. CARİLERİ TEDARİKÇİ OLARAK GÜNCELLE ────────────────────────────
def carileri_tedarikci_yap(token: str):
    print("\n🏢 Cariler tedarikçi olarak güncelleniyor...")
    guncellenen = 0
    sayfa = 1

    while True:
        try:
            sonuc = get(token, "/contacts", {
                "page[size]": 25,
                "page[number]": sayfa,
                "filter[account_type]": "customer"
            })
        except Exception as e:
            print(f"  ⚠️ Sayfa {sayfa} okunamadı: {e}")
            break

        cariler = sonuc.get("data", [])
        if not cariler:
            break

        for c in cariler:
            cid  = c["id"]
            isim = c.get("attributes", {}).get("name", "-")

            # "aa" gibi test carilerini atla
            if isim.lower() in ("aa", "test", "deneme"):
                print(f"  ⏭️ Atlandı (test cari): {isim}")
                continue

            try:
                basarili = patch(token, f"/contacts/{cid}", {
                    "data": {
                        "id":   cid,
                        "type": "contacts",
                        "attributes": {
                            "account_type": "supplier"
                        }
                    }
                })
                if basarili:
                    guncellenen += 1
                    print(f"  ✅ Tedarikçi yapıldı: {isim}")
                else:
                    print(f"  ❌ Güncellenemedi: {isim}")
            except Exception as e:
                print(f"  ❌ Hata ({isim}): {e}")

        if len(cariler) < 25:
            break
        sayfa += 1

    print(f"  Toplam güncellenen cari: {guncellenen}")
    return guncellenen

# ── ANA AKIŞ ──────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  PARAŞÜT TEMİZLEME SCRİPTİ")
    print("=" * 60)
    print(f"  Şirket ID: {COMPANY_ID}")
    print()

    onay = input("⚠️  Bu işlem GERİ ALINAMAZ. Devam etmek istiyor musun? (evet/hayir): ")
    if onay.lower() != "evet":
        print("İptal edildi.")
        sys.exit(0)

    print()
    print("Ne yapmak istiyorsun?")
    print("  1 — Sadece satış faturalarını sil")
    print("  2 — Sadece irsaliyeleri sil")
    print("  3 — Carileri tedarikçi yap")
    print("  4 — Hepsini yap (1+2+3)")
    secim = input("Seçim (1/2/3/4): ").strip()

    token = token_al()

    if secim in ("1", "4"):
        satis_faturalarini_sil(token)

    if secim in ("2", "4"):
        irsaliyeleri_sil(token)

    if secim in ("3", "4"):
        carileri_tedarikci_yap(token)

    print("\n✅ İşlem tamamlandı!")

if __name__ == "__main__":
    main()