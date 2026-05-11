"""
parasut_tedarikci_temizle.py — Paraşüt Tedarikçi ve Geçmiş Temizleme Scripti (DÜZELTİLMİŞ v2)
=============================================================================
Bu script şunları yapar:
1. Tüm Kasa/Banka hesaplarını tarar ve içindeki işlemleri (tahsilat/ödeme) siler.
2. Tüm alış faturalarını / giderleri (purchase_bills) siler.
3. Sadece TEDARİKCİ (supplier) tipindeki carileri siler.
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

CLIENT_ID = os.getenv("PARASUT_CLIENT_ID")
CLIENT_SECRET = os.getenv("PARASUT_CLIENT_SECRET")
USERNAME = os.getenv("PARASUT_USERNAME")
PASSWORD = os.getenv("PARASUT_PASSWORD")
COMPANY_ID = os.getenv("PARASUT_COMPANY_ID")
BASE_URL = "https://api.parasut.com/v4"


# ── TOKEN ─────────────────────────────────────────────────────────────
def token_al() -> str:
    print("🔑 Paraşüt token alınıyor...")
    res = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "username": USERNAME,
            "password": PASSWORD,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"
        }
    )
    res.raise_for_status()
    token = res.json()["access_token"]
    print("✅ Token alındı")
    return token


# ── API İSTEK MERKEZİ (RATE LIMIT KORUMALI) ───────────────────────────
def _api_request(method: str, token: str, path: str, params: dict = None):
    url = f"{BASE_URL}/{COMPANY_ID}{path}"
    headers = {"Authorization": f"Bearer {token}"}

    # 429 hatası alırsak en fazla 3 kez tekrar dener
    for attempt in range(3):
        time.sleep(0.5)  # Her istekte standart bekleme süresi
        if method == 'GET':
            res = requests.get(url, headers=headers, params=params or {})
        else:
            res = requests.delete(url, headers=headers)

        if res.status_code == 429:
            print(f"    ⏳ API sınırına ulaşıldı (429). 5 saniye bekleniyor... (Deneme: {attempt + 1}/3)")
            time.sleep(5)
            continue

        return res
    return res


def get(token: str, path: str, params: dict = None) -> dict:
    res = _api_request('GET', token, path, params)
    res.raise_for_status()
    return res.json()


def delete(token: str, path: str) -> bool:
    res = _api_request('DELETE', token, path)
    return res.status_code in (200, 204)


# ── 1. İŞLEMLERİ (TAHSİLAT/ÖDEME) SİL ─────────────────────────────────
def islemleri_sil(token: str):
    print("\n💸 Nakit işlemleri (Tahsilat/Ödeme) Kasa bazlı aranıp siliniyor...")
    silinen = 0

    try:
        hesaplar_sonuc = get(token, "/accounts", {"page[size]": 25})
        hesaplar = hesaplar_sonuc.get("data", [])
    except Exception as e:
        print(f"  ❌ Hesaplar okunamadı: {e}")
        return 0

    if not hesaplar:
        print("  ⚠️ Sistemde kasa/banka hesabı bulunamadı.")
        return 0

    for hesap in hesaplar:
        hesap_id = hesap["id"]
        hesap_adi = hesap.get("attributes", {}).get("name", "İsimsiz Hesap")
        print(f"\n  🏦 {hesap_adi} hesabı taranıyor...")

        islem_listesi = []
        sayfa = 1

        while True:
            try:
                sonuc = get(token, f"/accounts/{hesap_id}/transactions", {
                    "page[size]": 25,
                    "page[number]": sayfa
                })
                islemler = sonuc.get("data", [])
                if not islemler:
                    break

                for i in islemler:
                    # None değerini temiz string'e dönüştürüyoruz (NoneType hatası çözümü)
                    desc_val = i.get("attributes", {}).get("description")
                    desc_str = str(desc_val) if desc_val else "Açıklama Yok"

                    islem_listesi.append({
                        "id": i["id"],
                        "desc": desc_str
                    })

                if len(islemler) < 25:
                    break
                sayfa += 1
            except Exception as e:
                print(f"  ⚠️ İşlemler okunamadı: {e}")
                break

        if not islem_listesi:
            print("    ℹ️ Bu hesapta işlem yok.")
            continue

        for i in islem_listesi:
            iid = i["id"]
            try:
                if delete(token, f"/transactions/{iid}"):
                    silinen += 1
                    print(f"    ✅ İşlem Silindi: {iid} — {i['desc'][:30]}")
                else:
                    print(f"    ❌ Silinemedi: {iid}")
            except Exception as e:
                print(f"    ❌ Hata ({iid}): {e}")

    print(f"\n  Toplam silinen nakit işlemi: {silinen}")
    return silinen


# ── 2. ALIŞ FATURALARINI SİL ──────────────────────────────────────────
def alis_faturalarini_sil(token: str):
    print("\n🧾 Alış faturaları (Giderler) siliniyor...")
    silinen = 0
    fatura_listesi = []
    sayfa = 1

    while True:
        try:
            sonuc = get(token, "/purchase_bills", {
                "page[size]": 25,
                "page[number]": sayfa
            })
            faturalar = sonuc.get("data", [])
            if not faturalar:
                break

            for f in faturalar:
                desc_val = f.get("attributes", {}).get("description")
                desc_str = str(desc_val) if desc_val else "Açıklama Yok"

                fatura_listesi.append({
                    "id": f["id"],
                    "desc": desc_str
                })

            if len(faturalar) < 25:
                break
            sayfa += 1
        except Exception as e:
            print(f"  ⚠️ Faturalar okunamadı: {e}")
            break

    for f in fatura_listesi:
        fid = f["id"]
        try:
            if delete(token, f"/purchase_bills/{fid}"):
                silinen += 1
                print(f"  ✅ Fatura Silindi: {fid} — {f['desc'][:30]}")
            else:
                print(f"  ❌ Silinemedi: {fid}")
        except Exception as e:
            print(f"  ❌ Hata ({fid}): {e}")

    print(f"  Toplam silinen alış faturası: {silinen}")
    return silinen


# ── 3. TEDARİKÇİLERİ SİL ──────────────────────────────────────────────
def tedarikcileri_sil(token: str):
    print("\n🏢 Tedarikçiler siliniyor...")
    silinen = 0
    tedarikci_listesi = []
    sayfa = 1

    while True:
        try:
            sonuc = get(token, "/contacts", {
                "page[size]": 25,
                "page[number]": sayfa,
                "filter[account_type]": "supplier"
            })
            cariler = sonuc.get("data", [])
            if not cariler:
                break

            for c in cariler:
                tedarikci_listesi.append({
                    "id": c["id"],
                    "isim": c.get("attributes", {}).get("name", "-")
                })

            if len(cariler) < 25:
                break
            sayfa += 1
        except Exception as e:
            print(f"  ⚠️ Tedarikçiler okunamadı: {e}")
            break

    for c in tedarikci_listesi:
        cid = c["id"]
        isim = c["isim"]
        try:
            if delete(token, f"/contacts/{cid}"):
                silinen += 1
                print(f"  ✅ Tedarikçi silindi: {isim}")
            else:
                print(f"  ❌ Silinemedi (Bakiye veya farklı bir işlem kalmış olabilir): {isim}")
        except Exception as e:
            print(f"  ❌ Hata ({isim}): {e}")

    print(f"  Toplam silinen tedarikçi: {silinen}")
    return silinen


# ── ANA AKIŞ ──────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  PARAŞÜT TEDARİKÇİ & GEÇMİŞ TEMİZLEME SCRİPTİ")
    print("=" * 60)
    print(f"  Şirket ID: {COMPANY_ID}")
    print()

    onay = input("⚠️  Bu işlem GERİ ALINAMAZ. Devam etmek istiyor musun? (evet/hayir): ")
    if onay.lower() != "evet":
        print("İptal edildi.")
        sys.exit(0)

    print()
    print("Ne yapmak istiyorsun?")
    print("  1 — Sadece geçmiş işlemleri (Tahsilat/Ödeme) sil")
    print("  2 — Sadece alış faturalarını sil")
    print("  3 — Sadece tedarikçileri sil")
    print("  4 — Sırayla Hepsini Yap (1 -> 2 -> 3)")
    secim = input("Seçim (1/2/3/4): ").strip()

    token = token_al()

    if secim in ("1", "4"):
        islemleri_sil(token)

    if secim in ("2", "4"):
        alis_faturalarini_sil(token)

    if secim in ("3", "4"):
        tedarikcileri_sil(token)

    print("\n✅ İşlem tamamlandı!")


if __name__ == "__main__":
    main()