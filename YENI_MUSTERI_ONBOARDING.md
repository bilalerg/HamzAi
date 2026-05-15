# Yeni Müşteri Onboarding Rehberi

Profaix'e yeni bir müşteri eklendiğinde yapılacaklar, adım adım.

---

## Gerekli Bilgiler

Müşteriden şunları al:

| Bilgi | Nereden Bulunur |
|-------|-----------------|
| Profaix UUID | Profaix.com admin panelinden kullanıcı listesi |
| Ad / Firma adı | Müşteriden |
| Paraşüt Client ID | Paraşüt → Ayarlar → API Entegrasyonları |
| Paraşüt Client Secret | Aynı yer |
| Paraşüt Kullanıcı Adı | Paraşüt giriş e-postası |
| Paraşüt Şifresi | Müşteriden |
| Paraşüt Company ID | URL'den: `uygulama.parasut.com/{BURASI}/...` |
| Hurda Ürün ID | Paraşüt → Stok → Ürünler → ilgili ürün → URL'deki ID |

---

## Adım 1 — Profaix UUID'yi Bul

Profaix.com admin panelinden kullanıcı listesini çek. Müşterinin UUID'si şöyle görünür:
```
dd5f9592-bccc-47e0-b9d2-9f89b6056897
```

Yoksa müşteri profaix.com'a giriş yaptıktan sonra loglarda görünür:
```
✅ Otomatik giriş: Osman (dd5f9592-...)
```

---

## Adım 2 — Tenant Kaydını Oluştur

Aşağıdaki curl komutunu çalıştır (ADMIN_TOKEN'ı .env'den al):

```bash
curl -X POST https://chat.profaix.com/admin/tenant-ekle \
  -H "X-Admin-Token: ADMIN_TOKEN_BURAYA" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id":               "dd5f9592-bccc-47e0-b9d2-9f89b6056897",
    "ad":                      "Osman Firma Adı",
    "parasut_client_id":       "MÜŞTERİNİN_CLIENT_ID",
    "parasut_client_secret":   "MÜŞTERİNİN_CLIENT_SECRET",
    "parasut_username":        "osman@firma.com",
    "parasut_password":        "MÜŞTERİNİN_ŞİFRESİ",
    "parasut_company_id":      "123456",
    "parasut_hurda_product_id": "987654321",
    "ozellikler": {"dogru_parasut_akisi": false}
  }'
```

Başarılı yanıt:
```json
{"ok": true, "tenant_id": "dd5f9592-...", "ad": "Osman Firma Adı"}
```

---

## Adım 3 — `ozellikler` Alanını Ayarla

Müşterinin hangi akışı kullanacağına göre:

**Standart müşteri (doğru Paraşüt akışı — purchase_bills, inflow:true):**
```json
{"dogru_parasut_akisi": true}
```

**Osman gibi özel akış isteyen müşteri (sales_invoices, eski davranış):**
```json
{"dogru_parasut_akisi": false}
```

**Özelliği sonradan değiştirmek için** Supabase SQL Editor:
```sql
UPDATE tenants
SET ozellikler = '{"dogru_parasut_akisi": true}'
WHERE id = 'dd5f9592-bccc-47e0-b9d2-9f89b6056897';
```

---

## Adım 4 — Test Et

Müşteri profaix.com'dan giriş yapsın. Loglarda şunu görmen lazım:

```
✅ Otomatik giriş: Osman (dd5f9592-...)
Paraşüt'ten yeni token alınıyor (tenant=dd5f9592-...)
Paraşüt token alındı (tenant=dd5f9592-...)
Paraşüt'ten X cari çekildi (tenant=dd5f9592-...)
```

Eğer `tenant=bilaleroglu3663@gmail.com` görünüyorsa tenant kaydı okunamamıştır — Supabase'de `aktif = 1` olduğunu kontrol et:

```sql
SELECT id, ad, aktif, ozellikler FROM tenants WHERE id = 'dd5f9592-...';
```

---

## Adım 5 — Sorun Giderme

| Belirti | Sebep | Çözüm |
|---------|-------|-------|
| Fallback'e düşüyor | Tenant kaydı yok veya `aktif != 1` | SQL ile `aktif = 1` yap |
| "Cari bulunamadı" hatası | Yanlış Paraşüt hesabı bağlandı | Paraşüt bilgilerini kontrol et |
| Token alınamıyor | Yanlış username/password | Paraşüt'e manuel giriş yaparak doğrula |
| Fatura yanlış tipte | `dogru_parasut_akisi` yanlış | `ozellikler` JSON'unu güncelle |

---

## Özet Kontrol Listesi

- [ ] Profaix UUID alındı
- [ ] Paraşüt API bilgileri alındı (client_id, secret, username, password, company_id, hurda_product_id)
- [ ] `/admin/tenant-ekle` çağrıldı
- [ ] `ozellikler` alanı doğru set edildi
- [ ] Müşteri giriş yaptı, loglarda kendi tenant_id'si ile token aldığı görüldü
- [ ] Test fişi yüklendi, irsaliye ve fatura doğru tipte oluştu
