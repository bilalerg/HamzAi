# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Proje Özeti

**HamzAi / Profaix** — Türkçe muhasebe otomasyon ajanı. FastAPI tabanlı, Paraşüt ERP entegrasyonlu, Claude AI destekli web uygulaması. Kantar fişi OCR'ı, irsaliye/fatura otomasyonu ve chat arayüzü içerir.

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
PARASUT_CLIENT_ID
PARASUT_CLIENT_SECRET
PARASUT_USERNAME
PARASUT_PASSWORD
PARASUT_COMPANY_ID
PARASUT_HURDA_PRODUCT_ID
JWT_SECRET_KEY
ADMIN_TOKEN
ADMIN_USERNAME
ADMIN_PASSWORD
# Opsiyonel:
PROFAIX_EMBED_SECRET            # iframe embed akışı için
REQUIRE_SIGNED_EMBED_AUTH=1
ALLOWED_ORIGINS=https://profaix.com,https://chat.profaix.com
ENV=production                  # production'da güvensiz JWT/ADMIN değerler engellenir
BASE_URL                        # keep-alive ping (default: https://profaix-api.onrender.com)
```

## Mimari

```
main.py                  FastAPI app, tüm route'lar burada (~1700 satır)
app/
  core/
    config.py            Tüm env değişkenleri buradan okunur (tek kaynak)
    database.py          SQLAlchemy engine + tüm ORM modeller
    migrations.py        Manuel migration sistemi (startup'ta otomatik çalışır)
    logger.py            Loguru logger (stdout + dosya + errors.log)
  auth.py                JWT üretimi/çözümü, Profaix.com doğrulama,
                         tek-cihaz oturum (ActiveLogin tablosu)
  chatbox/
    llm_handler.py       Claude Haiku ile intent sınıflandırma (tool_use API)
  ocr/
    reader.py            Claude Sonnet ile kantar fişi OCR (vision API)
    plaka_kontrol.py     Plaka doğrulama ve duplicate fiş tespiti
  parasut/
    client.py            Paraşüt REST API istemcisi (senkron + async wrapper'lar)
    fatura.py            Fatura kesme (duplicate + race condition korumalı)
    irsaliye.py          İrsaliye oluşturma (duplicate + race condition korumalı)
chat.html                SPA chat arayüzü (~3000 satır, tek dosya)
login.html               Local admin giriş sayfası
```

## Temel Tasarım Kararları

### Auth Akışı
İki kullanıcı tipi:
- `profaix_user_id` → profaix.com'dan gelen kullanıcı (JWT'de `typ: "profaix"`)
- `id` → local admin (JWT'de `typ: "local"`)

`ActiveLogin` tablosu tek cihaz oturumunu zorlar. Yeni giriş eski cihazı düşürür → API `SESSION_REPLACED` kodu döner → frontend kullanıcıyı login'e yönlendirir.

Billing middleware: `/api/billing/` prefix'li endpoint'ler admin token ile de açık.

### Paraşüt Client — KRİTİK
Tüm fonksiyonlar senkron (`requests`). FastAPI async endpoint'lerinden **mutlaka** `async_*` wrapper kullan:

```python
# YANLIŞ — event loop bloklanır, tüm sistem donar
cariler = cari_listesi_getir()

# DOĞRU
cariler = await async_cari_listesi_getir()
```

Mevcut async wrapper'lar (`client.py` sonu):
`async_cari_listesi_getir`, `async_cari_cache_yenile`, `async_cari_id_bul`,
`async_parasut_post`, `async_parasut_delete`, `async_tum_bekleyen_irsaliyeleri_getir`,
`async_hesap_ozeti_getir`, `async_cari_detay_getir`, `async_islem_gecmisi_getir`,
`async_tahsilat_ekle`, `async_cari_faturalari_getir`, `async_fatura_detay_getir`

### LLM Modelleri
- OCR: `claude-sonnet-4-20250514` (`app/ocr/reader.py`, vision API, retry=2)
- Intent: `claude-haiku-4-5-20251001` (`app/chatbox/llm_handler.py`, tool_use API)

### Intent Sistemi
`INTENTLER` sözlüğü → `INTENT_ENUM` → tool_use → `intent_tespit` fonksiyonu.

Mevcut intent'ler:
`bekleyen_irsaliyeler`, `toplu_fatura`, `fatura_sorgula`, `satis_faturasi_kes`,
`tahsilat_ekle`, `odeme_ekle`, `yeni_cari`, `cari_detay`, `musteri_bakiyeleri`,
`hesap_ozeti`, `gunluk_ozet`, `son_fisler`, `none`

### Veritabanı Tabloları
`vehicles`, `weigh_tickets`, `waybills`, `invoices`, `chat_sessions`,
`active_logins`, `islem_logs`, `users`, `audit_logs`, `usage_logs`,
`billable_events`, `hata_logs`, `tahsilat_logs`, `ocr_hata_logs`, `giris_logs`

Bağlantı havuzu: `pool_size=5, max_overflow=10` (max 15, Supabase limiti).

### TicketStatus Akışı
```
ALINDI → OCR_TAMAMLANDI → IRSALIYE_OLUSTURULUYOR → FABRIKA_BEKLENIYOR
       → IRSALIYE_TAMAMLANDI → FATURA_KESILIYOR → TAMAMLANDI
       → HATA / IPTAL
```

### Billing Sistemi
`BillableEvent` tablosu — her faturalanabilir işlem `event_key` ile idempotent kaydedilir.
`/api/billing/usage/{user_id}` → kullanım istatistikleri (tarih filtreli).
`/api/billing/panel-click` → panel etkileşimi kaydı.

## Bilinen Kısıtlamalar

### Paraşüt API — Tarih Aralığı Desteklenmiyor
`filter[issue_date_gte]` ve `filter[issue_date_lte]` → **400 Bad Request**.
Tarih filtresi tüm veri Python'da uygulanır. Bunu değiştirme.

Desteklenen filtreler:
- `filter[contact_id]` ✅
- `filter[payment_status]` ✅
- `filter[issue_date]` ✅ (sadece tam eşleşme)
- `filter[issue_date_gte/lte]` ❌ desteklenmiyor

### Cari Cache
`force_refresh=True` her çağrıda tüm carileri Paraşüt'ten çeker, event loop bloklar. Kullanma.
Cache 15 dakikada bir otomatik yenilenir (`CACHE_SURE=900`).
Zorunlu yenileme: `await async_cari_cache_yenile()`.

### Duplicate Koruma
`irsaliye.py` ve `fatura.py`'de `IRSALIYE_OLUSTURULUYOR` / `FATURA_KESILIYOR` status kontrolü var.
Kaldırma — çift tıklama ve network retry'da para kaybı yaşanır.

### Cari Bulunamazsa
`fatura.py` ve `irsaliye.py`'de cari bulunamazsa işlem sert durdurulur (exception fırlatılır).
İlk cariyi fallback olarak kullanma — yanlış firmaya fatura kesilir.

## Sık Yapılan Değişiklikler

### Yeni Intent Eklemek
1. `app/chatbox/llm_handler.py` → `INTENTLER` sözlüğüne ekle, `SISTEM_PROMPTU` güncelle
2. `main.py` → endpoint yaz (`async def`, `await async_*` kullan)
3. `chat.html` → `intentHandlers` objesine JS handler ekle

### Yeni Paraşüt Çağrısı Eklemek
1. `app/parasut/client.py` → senkron fonksiyon yaz
2. `client.py` sonuna async wrapper ekle:
   ```python
   async def async_yeni_fonksiyon(param: str) -> dict:
       return await asyncio.to_thread(yeni_fonksiyon, param)
   ```
3. `main.py`'de `await async_yeni_fonksiyon(param)` ile çağır

### Yeni DB Tablosu / Kolonu Eklemek
1. `app/core/database.py` → `Base` miras alan model ekle
2. `app/core/migrations.py` → `calistir()` içine ekle:
   ```python
   _kolon_ekle(conn, text(
       "ALTER TABLE tablo ADD COLUMN IF NOT EXISTS kolon VARCHAR(100)"
   ), "kolon (tablo)")
   # veya tablo için:
   _tablo_olustur(conn, text("CREATE TABLE IF NOT EXISTS ..."), "tablo_adi")
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

# Muhasebe
GET  /api/cariler                    Cari listesi (?refresh=true)
GET  /api/hesap-ozeti                Özet (?cari=FirmaAdı)
GET  /api/islem-gecmisi/{cari}       İşlem geçmişi (?baslangic=&bitis=&sayfa=&limit=)
GET  /api/bekleyen-irsaliyeler       Bekleyenler (?baslangic=&bitis=&sayfa=&sayfa_basi=)
GET  /api/cari-faturalar/{cari}      Cari faturaları (?baslangic=&bitis=&durum=&sayfa=)
GET  /api/fatura-detay/{id}          Fatura detayı
POST /api/fatura-kes                 Tek fatura kes
POST /api/toplu-fatura-kes           Toplu fatura kes
POST /api/tahsilat                   Tahsilat ekle
POST /api/cari-olustur               Yeni cari
GET  /api/gunluk-ozet                Günlük özet
GET  /api/son-fisler                 Son 10 fiş

# Billing
GET  /api/billing/usage/{user_id}    Kullanım istatistikleri (?baslangic=&bitis=&limit=)
POST /api/billing/panel-click        Panel tıklama kaydı
GET  /api/kullanim-ozeti/{user_id}   Kullanım özeti (eski, deprecated)

# Admin (X-Admin-Token header gerekli)
GET  /admin/islem-loglari            İşlem logları (?limit=&islem_tipi=)
GET  /admin/konusmalar               Chat geçmişleri (?limit=)
GET  /admin/accounts-debug           Paraşüt hesapları (bağlantı testi)
GET  /admin/irsaliye-debug/{id}      İrsaliye ham verisi
POST /admin/reset-db                 DB temizle (⚠️ GERİ ALINAMAZ)
```

## Test

```bash
# Sistem sağlık
curl https://[render-url]/health

# Paraşüt bağlantısı
curl -H "X-Admin-Token: TOKEN" https://[render-url]/admin/accounts-debug

# Admin loglar
curl -H "X-Admin-Token: TOKEN" "https://[render-url]/admin/islem-loglari?limit=10"
```