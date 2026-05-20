# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Proje Özeti

**HamzAi / Profaix** — Türkçe muhasebe otomasyon ajanı. FastAPI tabanlı, Paraşüt ERP entegrasyonlu, Claude AI destekli web uygulaması. Kantar fişi OCR'ı, irsaliye/fatura otomasyonu, multi-tenant ve chat arayüzü içerir.

**Hedef sektör:** Hurda metal alım/satım. Hurdacıdan kantar fişi gelir → OCR ile okunur → Paraşüt'e irsaliye + fatura olarak kesilir.

## Çalıştırma

```bash
python -m venv .venv && .venv\Scripts\activate  # Windows
pip install -r requirements.txt

uvicorn main:app --reload --host 0.0.0.0 --port 8000  # geliştirme
uvicorn main:app --host 0.0.0.0 --port $PORT           # production (Render)
```

Uygulama başlarken `validate_config()` çalışır — tüm `.env` değerleri dolu olmazsa başlamaz.

## Gerekli .env Değişkenleri

```
ANTHROPIC_API_KEY
DATABASE_URL                    # PostgreSQL (Supabase)
PARASUT_CLIENT_ID               # Default tenant credentials (Bilal/admin)
PARASUT_CLIENT_SECRET
PARASUT_USERNAME
PARASUT_PASSWORD
PARASUT_COMPANY_ID
PARASUT_HURDA_PRODUCT_ID
JWT_SECRET_KEY
ADMIN_TOKEN
ADMIN_USERNAME
ADMIN_PASSWORD
CREDENTIAL_ENCRYPTION_KEY       # Tenant Paraşüt credential şifreleme (Fernet key)
                                 # Üretmek: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
                                 # Boş bırakılırsa credential'lar düz metin saklanır
OCR_MODEL                       # Opsiyonel — default claude-sonnet-4-20250514
# Opsiyonel:
PROFAIX_EMBED_SECRET            # iframe embed akışı için
REQUIRE_SIGNED_EMBED_AUTH=1
ALLOWED_ORIGINS=https://profaix.com,https://chat.profaix.com
ENV=production                  # production'da güvensiz JWT/ADMIN değerler engellenir
BASE_URL                        # keep-alive ping (default: https://profaix-api.onrender.com)
```

## Mimari

```
main.py                  FastAPI app + middleware + lifespan + router kayıtları (~160 satır)
                         (endpoint TANIMI YOK — sadece include_router çağrıları)
app/
  core/
    config.py            Tüm env değişkenleri buradan okunur (tek kaynak)
    database.py          SQLAlchemy engine + tüm ORM modeller
    migrations.py        Manuel migration sistemi (startup'ta otomatik çalışır)
    tenant.py            Multi-tenant credential yönetimi + özellik (feature flag) okuma
    logger.py            Loguru logger (stdout + dosya + errors.log)
  auth.py                JWT üretimi/çözümü, Profaix.com doğrulama,
                         tek-cihaz oturum (ActiveLogin tablosu)
  routers/               Tüm endpoint tanımları (APIRouter'lara bölünmüş)
    __init__.py          Boş (paket marker)
    deps.py              Paylaşılan helper'lar (_islem_log, _usage_kaydet, _billable_kaydet,
                         _tenant_id_from_user, _guvenli_hata, _admin_kontrol, tr_para, tr_kg,
                         canonical_chat_session_id, _json_gather, _PANEL_TIKLAMA_EVENT)
    auth.py              /health, /status, /chat, /login, /api/login, /api/logout, /api/me, /api/session/*
    chat.py              /api/chat
    ocr.py               /api/upload, /api/fis-iptal, /api/firma-sec
    cari.py              /api/cariler, /api/cari-bilgi, /api/cari-olustur, /api/cari-guncelle
    fatura.py            /api/fatura-detay, /api/cari-faturalar, /api/toplu-fatura-kes,
                         /api/fatura-kes, /api/fatura-iptal, /api/fatura-guncelle
    alis_fatura.py       /api/alis-faturasi-*, /api/bekleyen-alis-faturalari, /api/e-fatura-*
    tahsilat.py          /api/tahsilat, /api/tahsilat-sil, /api/odeme-ekle, /api/cari-odeme,
                         /api/para-transferi, /api/hesaplar, /api/stok-hareketleri
    rapor.py             /api/hesap-ozeti, /api/islem-gecmisi, /api/bekleyen-irsaliyeler,
                         /api/gunluk-ozet, /api/son-fisler, /api/gelir-gider-raporu
    billing.py           /api/billing/*, /api/kullanim-ozeti
    admin.py             /admin/* (X-Admin-Token gerekli)
  chatbox/
    llm_handler.py       Claude Haiku ile intent sınıflandırma (tool_use API)
  ocr/
    reader.py            Claude Sonnet ile kantar fişi OCR + confidence scoring
    plaka_kontrol.py     Plaka doğrulama ve duplicate fiş tespiti
  parasut/
    client.py            Paraşüt REST API istemcisi (~2600 satır, senkron + async wrapper'lar)
    fatura.py            Fatura kesme (duplicate + race condition korumalı)
    irsaliye.py          İrsaliye oluşturma (duplicate + race condition korumalı)
chat.html                HTML iskelet (~113 satır, sadece DOM yapısı + script tag'leri)
login.html               Local admin giriş sayfası
static/                  Frontend asset'leri (FastAPI StaticFiles ile /static/ altında serve edilir)
  css/
    chat.css             Tüm tema/layout CSS (~690 satır)
  img/
    logo.jpg             Header logosu (eski inline base64 yerine)
  js/                    Numaralandırılmış sıralı yüklenen modüller
    01-state.js          Global vars + DOM element refs
    02-core.js           Fetch intercept + UI helpers (panel, dialog, dropdown, addMessage)
    03-session.js        Auth, oturum yönetimi, sohbet kaydetme
    04-chat.js           sendMessage + OCR upload + firmaSecildi (confidence color)
    05-rapor.js          Tüm raporlar (özet, fişler, bakiyeler, hesap özeti)
    06-fatura.js         Fatura/irsaliye akışları (alış, iptal, güncelle, toplu, e-fatura)
    07-tahsilat.js       Tahsilat/ödeme/transfer formları
    08-cari.js           Cari işlemleri (detay, düzenle, yeni, cache yenile)
    09-bootstrap.js      window.onload
```

**JS modül yükleme sırası KRİTİK** — `<script>` tag'leri sıralı çalışır (no `defer`/`async`).
- `let SESSION_ID` (01-state) → tüm modüller okuyabilir (global lexical environment paylaşılır)
- `function panelKapat()` (02-core) → otomatik `window.panelKapat` olur → HTML `onclick="..."` çalışır
- Bootstrap token inject (`main.py` `_chat_html()`) **01-state.js'in önüne** yapılır — `let AUTH_TOKEN = (window.PROFAIX_BOOTSTRAP_TOKEN || '')` doğru değeri okur.

## Temel Tasarım Kararları

### Auth Akışı
İki kullanıcı tipi:
- `profaix_user_id` → profaix.com'dan gelen kullanıcı (JWT'de `typ: "profaix"`)
- `id` → local admin (JWT'de `typ: "local"`)

`ActiveLogin` tablosu tek cihaz oturumunu zorlar. Yeni giriş eski cihazı düşürür → API `SESSION_REPLACED` kodu döner → frontend kullanıcıyı login'e yönlendirir.

Billing middleware: `/api/billing/` prefix'li endpoint'ler admin token ile de açık.

### Multi-Tenant Mimari — KRİTİK

Her **profaix.com kullanıcısı kendi tenant'ı** olarak çalışır. `tenants` tablosu:
- `id` (profaix_user_id) — primary key
- `parasut_*` credentials (Fernet ile şifreli olabilir)
- `ozellikler` (JSONB) — per-tenant feature flag'leri

`_tenant_id_from_user(user)` → JWT'den profaix_user_id'yi alır, default tenant'a düşmez.
`_tenant_credentials(tid)` → tenant'ın kendi Paraşüt credentials'ını DB'den çeker.

Her Paraşüt çağrısında `tenant_id` parametre olarak iletilir → token, cache, locks **tenant başına ayrı tutulur** (`client.py` başındaki `_tokens`, `_cari_caches`, `_token_locks`).

### Tenant Özellik (Feature Flag) Sistemi

`tenants.ozellikler` JSONB alanı — şu an kullanılan tek flag:

```json
{"dogru_parasut_akisi": true}
```

`get_tenant_ozellik(tenant_id, "dogru_parasut_akisi", varsayilan=True)` ile okunur.

**`dogru_parasut_akisi=False` → Osman eski akışı:**
- İrsaliye daima `inflow=False` (giden — Osman müşteri-yönlü çalışır)
- Tüm faturalar daima `sales_invoices` (purchase_bills akışına girmez)
- Cari tip ayrımı yapılmaz (her cari `customer` varsayılır)
- `/api/tahsilat` tip kontrolü atlanır (defensive)

**`dogru_parasut_akisi=True` (default — yeni tenant'lar):**
- Kantar fişi → `inflow=True` (gelen mal, alış)
- Cari tipi tedarikçiyse → `purchase_bills` (alış faturası)
- Cari tipi müşteriyse → `sales_invoices` (satış faturası)

Bu flag yeni feature'ları eklerken her zaman **kontrol edilmeli**: `dogru_akis` False ise eski davranışa düşülmeli.

### Paraşüt Client — KRİTİK
Tüm fonksiyonlar senkron (`requests`). FastAPI async endpoint'lerinden **mutlaka** `async_*` wrapper kullan:

```python
# YANLIŞ — event loop bloklanır, tüm sistem donar
cariler = cari_listesi_getir()

# DOĞRU
cariler = await async_cari_listesi_getir(tenant_id=tenant_id)
```

Async wrapper'lar `client.py` sonunda toplu listelenir. Yeni fonksiyon eklenince hem senkron hem async versiyonu yazılmalı.

### Tip-Aware Fatura Akışı

Sistem genelinde **müşteri/tedarikçi ayrımı** Paraşüt V4 dokümana göre yapılır:
- Müşteri (satış) → `sales_invoices` + `relationships.contact`
- Tedarikçi (alış) → `purchase_bills` + `relationships.supplier` (filter[supplier_id])

Generic wrapper'lar:
- `fatura_detay_universal(fatura_id, tip="customer"|"supplier")`
- `fatura_iptal_universal(fatura_id, tip)`
- `fatura_guncelle_universal(fatura_id, tip, **alanlar)`
- `cari_faturalari_getir(cari_id, tip="customer"|"supplier"|"both")`

Endpoint'lerde `?tip=` query param veya body `{tip:...}` ile gelir, default `"customer"` (geriye uyumlu).

UI'da `tipSecimSor(baslik, aciklama, callback)` helper'ı kullanıcıya "Müşteri / Tedarikçi" seçtirir.

### Cari Listesi — İki Sorgulu Merge

Paraşüt'te bir cari hem müşteri hem tedarikçi listesinde görünebilir. Tek sorguda `account_type` tek bir değer döner → diğer rol kaybolur.

`_cari_listesi_api`:
1. `filter[account_type]=customer` ile çek → müşteri listesi
2. `filter[account_type]=supplier` ile çek → tedarikçi listesi
3. ID üzerinden merge → her iki listede varsa `account_type='customer_supplier'`

### Cari Hesap Bazlı Tahsilat/Ödeme — TERS ETİKET UYARISI

Paraşüt V4 dokümanındaki etiketler **YANILTICI** — gerçek davranış kullanıcı testiyle doğrulandı:

| Endpoint | Doküman etiketi | **Gerçek Paraşüt UI davranışı** |
|---|---|---|
| `contact_credit_transactions` | "Ödeme" | **"Tahsilat"** olarak yazılır, bakiye NEGATİFE gider |
| `contact_debit_transactions` | "Tahsilat" | **"Ödeme"** olarak yazılır, bakiye POZİTİFE gider |

Bizim kullanım:
- `cari_bazli_tahsilat()` → `contact_credit_transactions` (Paraşüt UI'da "Tahsilat" satırı)
- `cari_bazli_odeme()` → `contact_debit_transactions` (Paraşüt UI'da "Ödeme" satırı)

**Asla değiştirme** — dokümana güvenip ters çevirme; çok uzun debug süreci sonrası bu hâline gelmiştir.

### Bakiye Yönüne Göre UI Filtreleme

Paraşüt UI mantığı: bakiye yönüne göre Ödeme/Tahsilat butonları görünür.
- Bakiye 0 → ikisi de
- Bakiye negatif (biz borçluyuz) → sadece Ödeme
- Bakiye pozitif (cari bize borçlu) → sadece Tahsilat

Profaix tarafında:
- **Tahsilat formu** (`tahsilatFormGoster`): dropdown'da sadece `bakiye >= 0` cariler
- **Ödeme formu** (`odemeCariBazliFormGoster`): dropdown'da sadece `bakiye <= 0` cariler

### LLM Modelleri
- OCR: `claude-sonnet-4-20250514` (env `OCR_MODEL` ile override edilebilir)
  - max_tokens=2048, retry=2, temperature=0.0
  - Chain-of-thought prompt: AŞAMA 1 layout tasviri → AŞAMA 2 JSON
  - Image preprocessing: UnsharpMask + Contrast +50% + Sharpness +100% + upscale 2400px
  - Plaka halüsinasyon koruması: LAYOUT tasvirinde plaka geçmiyorsa null'a düşürülür
- Intent: `claude-haiku-4-5-20251001` (`app/chatbox/llm_handler.py`, tool_use API)
  - max_tokens=600 (uzun yetenek listesi cevapları için)

### OCR Confidence Scoring — KRİTİK

Her OCR çıktısı 5 güven skoru (0.0-1.0) ile gelir: `plaka`, `agirlik`, `fis_no`, `firma`, `genel`.

**Hybrid hesaplama** (`_confidence_hesapla()` — `app/ocr/reader.py`):
```
nihai_conf = Claude AI skoru × W_claude + Heuristik × (1 - W_claude)
```

Alan başına `W_claude` ağırlığı:
| Alan | W_claude | Sebep |
|---|:-:|---|
| plaka | %75 | Regex format-geçerliliği gerçek doğruluğu garanti etmez (`31K081` regex'e uyar ama yanlış okuma olabilir) |
| agirlik | %60 | Heuristik (3000-40000 kg range) güçlü sinyal |
| fis_no | %60 | Uzunluk-bazlı heuristik (≥8 karakter ideal) |
| firma | %50 | `firma_kaynak` heuristiği (ust_baslik vs firma_satiri) çok güçlü |

**Genel skor ağırlıkları**: plaka %30 + ağırlık %30 + fiş_no %15 + firma %15 + tarih %10

**`firma_kaynak` sinyali** — OCR prompt'u firma'nın nereden alındığını JSON'a yazar:
- `"firma_satiri"` → "Firma:" etiketli satırdan alındı → heuristik 0.90 (tedarikçi)
- `"ust_baslik"` → Fişin en üst başlığından alındı → heuristik 0.15 (KANTAR firması, tedarikçi DEĞİL)

`ust_baslik` durumunda kullanıcıya sarı uyarı gösterilir ("Firma X üst başlıktan alındı, dropdown'dan seçin").

**UI eşikleri** (`static/js/04-chat.js`):
- `> 0.85` → yeşil (kullanıcı dokunmaz)
- `0.40-0.85` → sarı (kontrol uyarısı)
- `≤ 0.40` → kırmızı (Kaydet butonu kilitli — kullanıcı düzeltmeden kaydedemez)

**KRİTİK kural**: Plaka veya ağırlık kırmızıysa form OTOMATİK düzenleme modunda açılır.
Form kullanıcı manuel olarak kırmızı alanı düzenleyene kadar "Kaydet" disabled.

**DB**: `weigh_tickets` tablosunda 4 Float kolon: `ocr_confidence_plaka`,
`ocr_confidence_agirlik`, `ocr_confidence_fis_no`, `ocr_confidence_genel`.

`firma_confidence` ve `firma_kaynak` şu an sadece response'ta gönderiliyor — DB'ye yazılmıyor
(genel skor içinde sayılıyor). Sonradan analiz gerekirse kolon eklenebilir.

### Intent Sistemi
`INTENTLER` sözlüğü → `INTENT_ENUM` → tool_use → `intent_tespit` fonksiyonu.

Mevcut intent'ler:
`bekleyen_irsaliyeler`, `toplu_fatura`, `fatura_sorgula`, `satis_faturasi_kes`,
`alis_faturasi_kes`, `fatura_iptal`, `e_fatura_kes`,
`tahsilat_ekle`, `odeme_ekle`, `yeni_cari`, `cari_detay`, `musteri_bakiyeleri`,
`hesap_ozeti`, `gunluk_ozet`, `son_fisler`, `none`

**Yeni intent eklenmedi — tahsilat_sil intent yok!** Para silme komutu LLM tarafından yanlış sınıflandırılırsa para kaybı; bilinçli olarak sadece manuel buton.

### Veritabanı Tabloları
`tenants`, `vehicles`, `weigh_tickets`, `waybills`, `invoices`, `chat_sessions`,
`active_logins`, `islem_logs`, `users`, `audit_logs`, `usage_logs`,
`billable_events`, `hata_logs`, `tahsilat_logs`, `ocr_hata_logs`, `giris_logs`

**`weigh_tickets` OCR Confidence kolonları** (`Float`):
- `ocr_confidence_plaka`, `ocr_confidence_agirlik`, `ocr_confidence_fis_no`, `ocr_confidence_genel`

Bağlantı havuzu: `pool_size=5, max_overflow=10` (max 15, Supabase limiti).

### TicketStatus Akışı
```
ALINDI → OCR_TAMAMLANDI → IRSALIYE_OLUSTURULUYOR → FABRIKA_BEKLENIYOR
       → IRSALIYE_TAMAMLANDI → FATURA_KESILIYOR → TAMAMLANDI
       → HATA / IPTAL
```

**Duplicate kontrolü** sadece şu statuslar için aktif (plaka_kontrol.py):
`IRSALIYE_OLUSTURULUYOR`, `IRSALIYE_TAMAMLANDI`, `FABRIKA_BEKLENIYOR`,
`FABRIKA_ITIRAZI`, `FATURA_KESILIYOR`, `TAMAMLANDI`.
`OCR_TAMAMLANDI` / `IPTAL` / `HATA` → duplicate sayılmaz (kullanıcı OCR sonrası iptal edebilir).

### Billing Sistemi
`BillableEvent` tablosu — her faturalanabilir işlem `event_key` ile idempotent kaydedilir.
`/api/billing/usage/{user_id}` → kullanım istatistikleri (tarih filtreli).
`/api/billing/panel-click` → panel etkileşimi kaydı.

## Bilinen Kısıtlamalar

### Paraşüt API — Tarih Aralığı Desteklenmiyor
`filter[issue_date_gte]` ve `filter[issue_date_lte]` → **400 Bad Request**.
Tarih filtresi tüm veri Python'da uygulanır. Bunu değiştirme.

Desteklenen filtreler:
- `filter[contact_id]` ✅ (sales_invoices)
- `filter[supplier_id]` ✅ (purchase_bills — contact_id fallback ile)
- `filter[payment_status]` ✅
- `filter[issue_date]` ✅ (sadece tam eşleşme)
- `filter[issue_date_gte/lte]` ❌ desteklenmiyor

### Cari Cache
`force_refresh=True` her çağrıda tüm carileri Paraşüt'ten çeker, event loop bloklar. Kullanma.
Cache 15 dakikada bir otomatik yenilenir (`CACHE_SURE=900`).
Zorunlu yenileme: `await async_cari_cache_yenile(tenant_id=tenant_id)`.

### Duplicate Koruma
`irsaliye.py` ve `fatura.py`'de `IRSALIYE_OLUSTURULUYOR` / `FATURA_KESILIYOR` status kontrolü var.
Kaldırma — çift tıklama ve network retry'da para kaybı yaşanır.

### Cari Bulunamazsa
`fatura.py` ve `irsaliye.py`'de cari bulunamazsa işlem sert durdurulur (exception fırlatılır).
İlk cariyi fallback olarak kullanma — yanlış firmaya fatura kesilir.

### Tahsilat Silme — Transaction ID Kullan
Paraşüt'te payment silmek için `DELETE /transactions/{id}` kullanılır, **payment ID ile değil**.
Backend `fatura_detay_getir` yanıtında `payments.transaction` include eder; her ödemenin `transaction_id` alanı vardır.
Frontend bunu kullanarak silme yapar.

## Sık Yapılan Değişiklikler

### Yeni Endpoint Eklemek (router'a)
1. Endpoint'in konusunu belirle → uygun router dosyasını seç (`app/routers/X.py`):
   - Fatura/irsaliye → `fatura.py` (satış) veya `alis_fatura.py` (alış/e-fatura)
   - Cari yönetimi → `cari.py`
   - Tahsilat/ödeme → `tahsilat.py`
   - Rapor → `rapor.py`
   - Admin → `admin.py`
   - OCR/upload → `ocr.py`
   - Auth/session → `auth.py`
2. Router dosyasında `@router.X("/api/...")` decorator'ı ile fonksiyonu ekle
3. Gerekli helper'lar → `from app.routers.deps import ...` (zaten import edilmiş)
4. `main.py`'a dokunma — `include_router` zaten kurulu.
5. Yeni router dosyası eklersen `main.py`'daki `from app.routers import ...` satırına ekle ve
   `app.include_router(yeni_router.router)` ekle.

### Yeni Intent Eklemek
1. `app/chatbox/llm_handler.py` → `INTENTLER` sözlüğüne ekle, `SISTEM_PROMPTU` güncelle
2. `app/routers/chat.py` veya ilgili router → endpoint yaz (`async def`, `await async_*` kullan)
3. `static/js/04-chat.js` → `INTENT_MAP` ve `intentHandlers` objesine JS handler ekle
   (eskiden `chat.html`'de tek dosya idi — şimdi `static/js/04-chat.js` modülünde)

### Frontend Değişikliği Yapmak

Tek monolitik `chat.html` artık yok — değişiklik tipine göre uygun dosyaya yaz:

| İhtiyaç | Dosya |
|---|---|
| HTML iskelet değişikliği (yeni buton/div) | `chat.html` (~113 satır) |
| Stil/tema değişikliği | `static/css/chat.css` |
| Yeni global helper / state | `static/js/02-core.js` |
| Yeni intent / chat akışı | `static/js/04-chat.js` |
| Yeni rapor ekranı | `static/js/05-rapor.js` |
| Fatura/irsaliye işlemi | `static/js/06-fatura.js` |
| Tahsilat/ödeme formu | `static/js/07-tahsilat.js` |
| Cari işlemi | `static/js/08-cari.js` |

**`onclick="X()"` HTML attribute'ları** function declaration'larına bağlı —
otomatik olarak `window.X` olur, herhangi bir modülde tanımlı olabilir.

**Yeni JS modülü EKLEMEK istersen** (örn. `10-yeni.js`):
1. `static/js/10-yeni.js` oluştur
2. `chat.html` script tag listesine ekle (sıra önemli — bağımlı olduğu modülden SONRA gelmeli, en sonda `09-bootstrap.js` kalmalı)

### Yeni Paraşüt Çağrısı Eklemek
1. `app/parasut/client.py` → senkron fonksiyon yaz, `tenant_id=None` parametresi al
2. `client.py` sonuna async wrapper ekle:
   ```python
   async def async_yeni_fonksiyon(param: str, tenant_id=None) -> dict:
       return await asyncio.to_thread(yeni_fonksiyon, param, tenant_id)
   ```
3. `main.py`'de `tenant_id = _tenant_id_from_user(aktif_user)` → `await async_yeni_fonksiyon(param, tenant_id=tenant_id)` ile çağır
4. **Müşteri/tedarikçi ayrımı** gerekiyorsa `dogru_akis` flag'ini kontrol et

### Yeni DB Tablosu / Kolonu Eklemek
1. `app/core/database.py` → `Base` miras alan model ekle (yeni kolonsa mevcut modele alan ekle)
2. `app/core/migrations.py` → `calistir()` içine ekle:
   ```python
   _kolon_ekle(conn, text(
       "ALTER TABLE tablo ADD COLUMN IF NOT EXISTS kolon VARCHAR(100)"
   ), "kolon (tablo)")
   # veya tablo için:
   _tablo_olustur(conn, text("CREATE TABLE IF NOT EXISTS ..."), "tablo_adi")
   ```

**Gerçek örnek** — OCR confidence kolonları (`weigh_tickets`):
```python
# database.py — WeighTicket modelinde Float kolon
from sqlalchemy import Float
ocr_confidence_plaka   = Column(Float, nullable=True)
ocr_confidence_agirlik = Column(Float, nullable=True)
ocr_confidence_fis_no  = Column(Float, nullable=True)
ocr_confidence_genel   = Column(Float, nullable=True)

# migrations.py — startup'ta otomatik ekleyen migration
_kolon_ekle(conn, text(
    "ALTER TABLE weigh_tickets ADD COLUMN IF NOT EXISTS ocr_confidence_plaka FLOAT"
), "ocr_confidence_plaka (weigh_tickets)")
# ... diğer 3 kolon için aynı pattern
```

### Yeni Tenant Eklemek (Manuel)
Bir yeni müşteri için Supabase SQL:

```sql
INSERT INTO tenants (
    id, ad,
    parasut_client_id, parasut_client_secret,
    parasut_username, parasut_password,
    parasut_company_id, parasut_hurda_product_id,
    ozellikler, aktif, created_at, updated_at
) VALUES (
    'PROFAIX_USER_ID',                       -- profaix.com kullanıcı UUID
    'Müşteri Firma Adı',
    'PARASUT_CLIENT_ID',
    'PARASUT_CLIENT_SECRET',                  -- Fernet ile şifreliyse 'gAAAAA' ile başlar
    'parasut_username@example.com',
    'PARASUT_PASSWORD',
    'PARASUT_COMPANY_ID',
    'PARASUT_HURDA_PRODUCT_ID',
    '{"dogru_parasut_akisi": true}',          -- Yeni tenant default; Osman için false
    1, NOW(), NOW()
)
ON CONFLICT (id) DO UPDATE SET
    parasut_client_id = EXCLUDED.parasut_client_id,
    parasut_client_secret = EXCLUDED.parasut_client_secret,
    parasut_username = EXCLUDED.parasut_username,
    parasut_password = EXCLUDED.parasut_password,
    parasut_company_id = EXCLUDED.parasut_company_id,
    ozellikler = EXCLUDED.ozellikler,
    updated_at = NOW();
```

## Endpoint Referansı

```
# Genel
GET  /health                         Sağlık kontrolü
GET  /status                         Fiş istatistikleri
GET  /chat                           SPA arayüzü (auth gerekli)
GET  /login                          Login sayfası

# Auth
POST /api/login                      Giriş (profaix_user_id veya username/password)
POST /api/logout                     Çıkış
GET  /api/me                         Oturum bilgisi

# Session
GET  /api/session/{session_id}       Chat geçmişi
POST /api/session/append             Geçmişe satır ekle
DEL  /api/session/{session_id}       Geçmişi sil

# Chat & OCR
POST /api/chat                       Intent sınıflandırma
POST /api/upload                     Fiş fotoğrafı yükle (OCR)
POST /api/firma-sec                  Firma seç → irsaliye oluştur
POST /api/fis-iptal                  OCR sonrası fiş iptal — {ticket_id}

# Muhasebe — Cari
GET  /api/cariler                    Cari listesi (?refresh=true&tip=customer|supplier|both)
GET  /api/cari-bilgi/{cari}          Cari detay (iletişim, adres, vergi, bakiye, tip)
POST /api/cari-olustur               Yeni cari — {firma_adi, tip, email?, telefon?, ...}
POST /api/cari-guncelle              Cari güncelle — {cari_id|cari_adi, firma_adi?, tip?, ...}

# Muhasebe — Fatura
GET  /api/cari-faturalar/{cari}      Cari faturaları (?tip=customer|supplier|both&baslangic=&bitis=&durum=&sayfa=)
GET  /api/fatura-detay/{id}          Fatura detayı (?tip=customer|supplier)
POST /api/fatura-kes                 Tek satış faturası kes (irsaliyeden)
POST /api/toplu-fatura-kes           Toplu satış faturası kes
POST /api/fatura-iptal               Fatura iptal — {fatura_id, tip?}  (sales/purchase generic)
POST /api/fatura-guncelle            Fatura güncelle — {fatura_id, tip?, aciklama?, issue_date?, due_date?, currency?, invoice_no?, withholding_rate?, billing_address?, contact_id?, ...} (Paraşüt V4'ün tüm alanları)
POST /api/alis-faturasi-kes          Basit alış faturası kes — {cari_adi, tutar, ...}
POST /api/alis-faturasi-iptal        Alış faturası iptal (eski, tip='supplier' ile /api/fatura-iptal de çalışır)
GET  /api/alis-faturasi-detay/{id}   Alış faturası detayı
GET  /api/e-fatura-mukellef/{vkn}    VKN e-fatura mükellef sorgusu
POST /api/e-fatura-kes               Satış faturasını e-fatura/e-arşiv olarak resmileştir

# Muhasebe — Tahsilat / Ödeme
POST /api/tahsilat                   Tahsilat ekle — açık fatura varsa onlara, yoksa cari hesabına direkt
POST /api/tahsilat-sil               Tahsilat sil — {odeme_id, transaction_id?}
POST /api/odeme-ekle                 Ödeme ekle (alış faturasına) — {fatura_id, tutar, kasa_id?, tarih?, notlar?}
POST /api/cari-odeme                 Tedarikçi cari hesabına direkt ödeme — {cari_adi|cari_id, tutar, ...}
POST /api/para-transferi             Hesaplar arası transfer

# Muhasebe — Raporlar
GET  /api/hesap-ozeti                Özet (?cari=FirmaAdı)
GET  /api/islem-gecmisi/{cari}       İşlem geçmişi (sales + purchase birleştirilmiş)
GET  /api/bekleyen-irsaliyeler       Bekleyenler (?baslangic=&bitis=) — default son 12 ay
GET  /api/bekleyen-alis-faturalari   Ödenmemiş alış faturaları
GET  /api/gunluk-ozet                Günlük özet
GET  /api/son-fisler                 Son 10 fiş
GET  /api/hesaplar                   Kasa/banka hesap listesi
GET  /api/stok-hareketleri           Stok hareketleri (?urun_id=&limit=)
GET  /api/gelir-gider-raporu         Gelir/gider raporu (?baslangic=&bitis=)

# Billing
GET  /api/billing/usage/{user_id}    Kullanım istatistikleri
POST /api/billing/panel-click        Panel tıklama kaydı

# Admin (X-Admin-Token header gerekli)
GET  /admin/islem-loglari            İşlem logları
GET  /admin/konusmalar               Chat geçmişleri
GET  /admin/accounts-debug           Paraşüt hesapları bağlantı testi
GET  /admin/cari-debug               Cari listesi raw attribute (debug)
GET  /admin/irsaliye-debug/{id}      İrsaliye ham verisi
POST /admin/reset-db                 DB temizle (⚠️ GERİ ALINAMAZ)
```

## Test

```bash
# Sistem sağlık
curl https://[render-url]/health

# Paraşüt bağlantısı
curl -H "X-Admin-Token: TOKEN" https://[render-url]/admin/accounts-debug

# Cari raw data
curl -H "X-Admin-Token: TOKEN" https://[render-url]/admin/cari-debug

# Admin loglar
curl -H "X-Admin-Token: TOKEN" "https://[render-url]/admin/islem-loglari?limit=10"
```

---

## 🚧 ROADMAP — Henüz YAPILMAMIŞ

### FASE 1: Rol & Yetki Sistemi (RBAC) — KRİTİK SONRAKİ ADIM

**Bağlam:** Profaix gerçek müşterilere açılıyor. Her firma içinde **farklı kullanıcı tipleri** olacak:
- `owner` (firma sahibi) — her şeye erişim
- `muhasebeci` — fatura, tahsilat, rapor (patron işleri)
- `sofor` — sadece kantar fişi yükleme (TIR şoförü fotoğraf atıyor)
- `personel` — sınırlı (bakiye sorgu, rapor görüntüleme)

**Mimari (hibrit):**
1. **Profaix.com'dan gelen `role`** birincil kaynak (mevcut user API'sinde `"role": "user"` döndürüyor — Profaix.com'da detaylandırılabilir)
2. **`tenant_users` tablosu** firma içi role override için:
   ```sql
   CREATE TABLE tenant_users (
     profaix_user_id TEXT NOT NULL,
     tenant_id TEXT NOT NULL,
     rol VARCHAR(50) NOT NULL,        -- 'owner' / 'muhasebeci' / 'sofor' / 'personel'
     yetkiler JSONB,                  -- ['fis_yukle', 'fatura_kes', ...] override
     aktif INTEGER DEFAULT 1,
     PRIMARY KEY (profaix_user_id, tenant_id)
   );
   ```
3. **Bir kullanıcı farklı tenant'larda farklı role** alabilir (Bilal kendi firmasında owner, Osman'ın firmasında muhasebeci olabilir)

**Yetki Matrisi (default mapping):**

| İşlem | owner | muhasebeci | sofor | personel |
|---|:-:|:-:|:-:|:-:|
| Fiş yükleme (OCR) | ✅ | ✅ | ✅ | ❌ |
| Bekleyen irsaliyeler | ✅ | ✅ | ❌ | ❌ |
| Fatura kesme | ✅ | ✅ | ❌ | ❌ |
| Fatura iptal | ✅ | ✅ | ❌ | ❌ |
| Tahsilat/Ödeme | ✅ | ✅ | ❌ | ❌ |
| Cari yönet | ✅ | ✅ | ❌ | ❌ |
| Raporlar | ✅ | ✅ | ❌ | ✅ |
| E-Fatura | ✅ | ✅ | ❌ | ❌ |
| Kullanıcı yönetimi | ✅ | ❌ | ❌ | ❌ |

**Uygulama adımları:**
1. `auth.py` → JWT'ye role bilgisi işle (`/api/me` ile dön)
2. Backend decorator `@require_permission("fis_yukle")` her endpoint'te
3. Frontend `chat.html` → `/api/me` döndüğünde `yetkiler` array'i al, sol menü buton/grupları filtrele
4. `tenant_users` tablosu + admin endpoint'i (firma sahibi diğer kullanıcılara role atayabilsin)

### FASE 2: Tenant Feature Flag (Modül Aç/Kapa)

`tenants.ozellikler` JSON'unda hangi modüller aktif:
```json
{
  "dogru_parasut_akisi": true,
  "modules": {
    "ocr": true,
    "fatura": true,
    "tahsilat": true,
    "e_fatura": false,
    "stok_takibi": true,
    "rapor": true
  }
}
```

**Önemli:** Bir işlem yapmak için **hem tenant açmış olmalı hem kullanıcının yetkisi olmalı** (AND mantığı).

### FASE 3: Self-Service Onboarding

Yeni müşteri ilk girişte "Paraşüt'ünüzü bağlayın" formu:
- Client ID, Secret, Email, Password, Company ID, Hurda Product ID
- Sistem test eder (token alır → ✓/✗)
- Başarılı ise DB'ye yazar (Fernet ile şifreli)

Şu an manuel SQL ile yapılıyor (yukarıdaki "Yeni Tenant Eklemek" bölümü).

### FASE 4: Production Hardening

Demo öncesi NOT eklenmiş ama yapılmamış:
- **Sentry** entegrasyonu (`sentry-sdk[fastapi]`) — production hata bildirimi
- **Rate limiting** (`slowapi`) — `/api/upload`, `/api/chat` için kullanıcı başına
- **Otomatik testler** (pytest) — kritik para işleyen akışlar
- **OCR kullanıcı başına günlük quota** — abuse koruması
- **CSRF token** — iframe embed akışı için
- **CREDENTIAL_ENCRYPTION_KEY production'da zorunlu hâle getir** — env yoksa startup hatası

### FASE 5: KVKK / Veri Koruma

- Logger'da PII (IP, kullanıcı adı) mevcut → loglama policy
- Tenant veri export/silme akışı (GDPR right to be forgotten)

---

## 📚 Önemli Tarihsel Kararlar (Yeniden Açmaktan Kaçın)

1. **`contact_credit_transactions` = Tahsilat, `contact_debit_transactions` = Ödeme** — Paraşüt dokümanı YANILTICI, gerçek davranış böyle. KAPTAN DEMİR testiyle doğrulandı.
2. **`unarchive` action endpoint'i** — `recover_from_archive` yerine V4 doc'a uygun. Fallback olarak eski endpoint de denenir.
3. **`tahsilat_sil` intent yok** — LLM yanlış sınıflandırırsa para kaybı; sadece manuel buton.
4. **Bekleyen irsaliyeler default 12 ay** — eskiden "bu ayın 1'i" idi, eski faturalanmamış irsaliyeler kayboluyordu.
5. **Image preprocessing zorunlu** — küçük fotoğrafta upscale + unsharp + contrast olmadan OCR halüsinasyon yapıyor.
6. **OCR plaka doğrulama** — Claude'un verdiği plaka LAYOUT tasvirinde geçmiyorsa null'a düşürülür (önceki fişten "hatırlanan" plakaları engeller).
7. **Cari listesi iki sorgulu merge** — Paraşüt tek sorguda `account_type` tek değer döndürür, hem müşteri hem tedarikçi olan cari için merge gerekir.
8. **`dogru_parasut_akisi=False` her yeni feature'da kontrol edilir** — Osman gibi eski tenant'lar geriye uyumlu olmak zorunda.
9. **chat.html monolit → modüler refactor** — eski 4871 satırlık tek dosya `chat.html` (113 satır iskelet) + `static/css/chat.css` (~690 satır) + 9 numaralı `static/js/0X-*.js` modülüne bölündü. JS modülleri `<script>` tag sırasıyla yüklenir, paylaşılan global lexical environment kullanır. Eski monolite geri dönme — `chat.html.bak` yedek var ama `.gitignore`'da.
10. **OCR Confidence ≠ Doğruluk Garantisi** — Plaka için `^\d{2}[A-Z]{1,3}\d{2,4}$` regex match yüksek heuristik vermez (yanlış okumalar da regex'e uyabilir, örn `37LK081` yerine `31K081` okundu — ikisi de geçerli format). Bu yüzden plaka için Claude ağırlığı %75'e çıkarıldı, heuristik regex match'i %70 maksimumda tutulur.
11. **Firma kaynak tespiti** — OCR prompt'u firma'nın `firma_satiri` (Firma: etiketinden) veya `ust_baslik` (en üst başlık) hangisinden geldiğini raporlar. `ust_baslik` durumunda firma KANTAR firmasıdır (KAPTAN DEMİR gibi), tedarikçi DEĞİL — heuristik 0.15 + sarı uyarı gösterilir. Kullanıcı dropdown'dan gerçek tedarikçiyi seçer.
12. **Low confidence ≠ retry** — Düşük güven OCR'ı yeniden çalıştırma sebebi DEĞİL (fotoğraf kalitesi sorunu, ikinci deneme aynı sonucu verir). Retry sadece JSON parse hatasında yapılır. Düşük güven kullanıcıya gösterilir → manuel düzeltir.
13. **main.py monolit → APIRouter modülleri** — eski 2683 satırlık tek dosya `main.py` (161 satır, sadece app + middleware + lifespan + include_router) + 10 router dosyası (`app/routers/X.py`) + ortak `deps.py`'a bölündü. Yeni endpoint eklemek için **main.py'a DOKUNMA** — uygun router dosyasına `@router.X` ile ekle. `include_router` zaten kurulu. Eski monolite geri dönme — `main.py.bak` yedek var (`.gitignore`'da `*.bak`).
