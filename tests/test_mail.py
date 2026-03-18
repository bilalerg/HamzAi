import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.mail.sender import fabrikaya_irsaliye_gonder, muhasebeciye_uyari_gonder
from app.core.config import validate_config

validate_config()

print("\n📧 Mail testi başlıyor...")
print("-" * 50)

# Test 1 — Fabrikaya irsaliye maili
ref_kodu = fabrikaya_irsaliye_gonder(
    fabrika_mail="fabrika@test.com",
    waybill_id=1,
    plaka="31P1585",
    agirlik_kg=29400,
    tarih="24.12.2025",
    irsaliye_no="UHTUFUK276"
)
print(f"✅ İrsaliye maili gönderildi: [{ref_kodu}]")

# Test 2 — Muhasebeciye uyarı
muhasebeciye_uyari_gonder(
    muhasebeci_mail="muhasebe@test.com",
    konu="Yeni Plaka Kaydedildi",
    mesaj="31P1585 plakalı araç sisteme eklendi."
)
print(f"✅ Muhasebeci uyarısı gönderildi")

print("\n🌐 Mailhog arayüzünden kontrol et: http://localhost:8025")

# Mevcut kodun altına ekle

print("\n" + "=" * 50)
print("🧪 Parser testi başlıyor...")
print("=" * 50)

from app.mail.parser import ref_kodu_bul, nlp_karar_ver

# Test 1 — Ref kodu bulma
print("\n📌 Test 1: Ref kodu bulma")
test_konular = [
    ("Re: İrsaliye Onayı [Ref: HZ-0001]", "Onaylıyorum"),
    ("irsaliye hakkında", "Merhaba [Ref: HZ-0042] onaylıyorum"),
    ("Merhaba", "Teşekkürler"),  # Ref kodu yok
]

for konu, govde in test_konular:
    ref = ref_kodu_bul(konu, govde)
    print(f"  Konu: '{konu[:30]}...' → Ref: {ref}")

# Test 2 — NLP karar
print("\n📌 Test 2: NLP karar (Claude)")
test_mailler = [
    "Onaylıyorum, teşekkürler.",
    "Tamam, kabul ediyoruz.",
    "Bu irsaliyeye itiraz ediyoruz, ağırlık yanlış.",
    "Reddediyoruz.",
]

for mail in test_mailler:
    karar = nlp_karar_ver(mail)
    print(f"  Mail: '{mail}' → Karar: {karar}")