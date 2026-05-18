# reader.py — OCR Engine

import anthropic
import asyncio
import base64
import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict
from datetime import datetime

from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

OCR_MODEL      = "claude-sonnet-4-20250514"
OCR_MAX_TOKENS = 1024
OCR_MAX_RETRY  = 2  # Başarısız OCR'da kaç kez tekrar denensin


@dataclass
class FisVerisi:
    plaka:            Optional[str]           = None
    fis_no:           Optional[str]           = None
    net_agirlik_kg:   Optional[int]           = None
    net_tartim_kg:    Optional[int]           = None
    fire_kg:          Optional[int]           = None
    tarih:            Optional[str]           = None
    firma:            Optional[str]           = None
    irsaliye_no:      Optional[str]           = None
    kantar_no:        Optional[str]           = None
    malzemeler:       Optional[Dict[str,int]] = None
    claude_ham_cikti: Optional[str]           = None
    hata:             Optional[str]           = None
    plaka_uyari:      Optional[str]           = None


OCR_SYSTEM = """Sen Türkçe kantar fişlerini okuyan bir OCR motorusun. Görevin: fotoğraftaki METNİ harfi harfine çıkarmak.

ANA KURAL — OKU, AMA UYDURMA:
1. Gördüğün her şeyi DİKKATLİCE OKU. Harfler/rakamlar bulanık olsa bile ayırt edebiliyorsan değeri yaz.
2. Bir alanda HİÇBİR şey göremiyorsan SADECE O ZAMAN null bırak.
3. Önceki fişlerden bilgi HATIRLAMA. Her fotoğraf bağımsızdır.
4. "34BEG354", "1234567" gibi tipik/örnek değerler UYDURMA. Sadece fişten okuduğunu yaz.

VARSAYILAN DAVRANIŞ:
Profesyonel bir OCR motoru fişin %80'ini okur. Sen de en az o kadarını okumaya çalış.
"Bulanık" diye topyekûn null bırakmak YANLIŞTIR. Görebildiğin her alanı doldur.

ÇIKTI FORMATI:
Yanıtın SADECE '{' ile başlamalı ve '}' ile bitmelidir. Açıklama, başlık veya markdown YOK."""

OCR_PROMPT = """Aşağıdaki Türkiye hurda/geri dönüşüm sektörüne ait kantar fişini oku.

JSON şeması (alanları gerçekten okuyamadığın yerlerde null kullan):
{"plaka":"araç plakası","fis_no":"fiş no","net_tartim_kg":SAYI,"fire_kg":SAYI_YA_DA_0,"tarih":"GG.AA.YYYY","firma":"şirket adı","irsaliye_no":null,"kantar_no":null,"malzemeler":{}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FİRMA (tedarikçi/satıcı)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Öncelik sırası:
1. "Firma:", "Firma Adı:", "Tedarikçi:", "Gönderen:" etiketli satır VARSA → o satırdaki adı al
2. Böyle bir alan YOKSA → en üstteki başlık şirket adını al

DİKKAT:
- "Nakliye Firması:", "Taşıyıcı:", "Şoför:", "Ad Soyad:", "Operator:" → bunlar firma DEĞİL
- Fişte birden fazla şirket görüyorsan "Firma:" / "Tedarikçi:" etiketli olanı öncele
- Türkçe karakterler önemli: ŞŞ ÜÜ ÖÖ ÇÇ İİ GĞ — bunları da doğru aktar

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLAKA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- "Araç Plaka", "Plaka", "Araç-Dorse Plaka" etiketli alanı bul
- İki plaka tire/boşlukla ayrılmışsa (örn: "37 LK 081 - 34 BSE 559") → SADECE İLKİNİ al: 37LK081
- Format: boşluk/tire OLMADAN büyük harf → 37LK081, 06FH8714, 34BSE559
- Tek tek karakter okuyabiliyorsan plakayı yaz; göremezsen null

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AĞIRLIK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- net_tartim_kg: "Net Tartım", "Net", "BRÜT - TARA" satırındaki sayı
- Eğer fişte ayrı ayrı "BRÜT: 33000" ve "DARA: 17850" görüyorsan, NET satırını da BİLE oku;
  yazılmadıysa BRÜT - DARA hesabıyla bulabilirsin (15150 gibi)
- fire_kg: "Fire", "Tenzil" satırı — yoksa 0
- Tam sayı, nokta/virgül yok → 15150 ✓   15.150 ✗

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FİŞ NO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- "Fiş No", "No", "Sıra No" veya "Id." etiketli satırdaki numara
- Tipik formatlar: 130002187968, 9495, 1356
- Okuyamıyorsan null — örnek/standart numara uydurma

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TARİH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- "Tarih", "Geliş Tarihi", "Giriş Tarihi" alanından oku
- Format: GG.AA.YYYY → 12.03.2026
- Saat varsa atla, sadece tarih

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MALZEMELER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Mal cinsi listesi varsa oku: {"KARMA": 15150, "DKP": 3200}
- Liste yoksa veya tek malzeme varsa boş bırak: {}
- "Fire", "Tenzil" satırlarını malzeme olarak EKLEME

ÖNEMLİ: Görebildiğin alanları DOLDUR. "Emin değilim" diye topyekûn null bırakma — bu sistemi bozar.
Sadece tek bir karakter dahi okuyamadığın alanları null bırak."""


def _fotograf_to_base64(fotograf_yolu: str) -> tuple[str, str, dict]:
    """
    Fotoğrafı base64'e çevirir.
    Üçüncü dönüş: kalite metaverileri ({width, height, kb, dusuk_kalite}).
    """
    meta = {"width": 0, "height": 0, "kb": 0, "dusuk_kalite": False}
    try:
        from PIL import Image
        import io as _io
        with Image.open(fotograf_yolu) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            orig_w, orig_h = img.size
            # Çok küçük gelen fotoğrafları upscale et (Claude detayları daha iyi okusun).
            # thumbnail upscale yapmaz, manuel resize gerekiyor.
            if max(orig_w, orig_h) < 1000:
                oran = 1500 / max(orig_w, orig_h)
                yeni_boyut = (int(orig_w * oran), int(orig_h * oran))
                img = img.resize(yeni_boyut, Image.LANCZOS)
                logger.info(f"Küçük fotoğraf upscale edildi: {orig_w}x{orig_h} → {img.size}")
            else:
                img.thumbnail((1500, 1500), Image.LANCZOS)
            buffer = _io.BytesIO()
            img.save(buffer, format="JPEG", quality=90)
            data = base64.b64encode(buffer.getvalue()).decode("utf-8")
            meta["width"]  = img.size[0]
            meta["height"] = img.size[1]
            meta["kb"]     = len(data) // 1024
            # Original çok küçükse veya base64 boyutu çok düşükse kalite uyarısı
            if max(orig_w, orig_h) < 800 or meta["kb"] < 60:
                meta["dusuk_kalite"] = True
            logger.info(
                f"Fotoğraf boyutlandırıldı: orig=({orig_w}x{orig_h}) "
                f"final={img.size} | {meta['kb']}KB base64 | "
                f"düşük_kalite={meta['dusuk_kalite']}"
            )
        return data, "image/jpeg", meta
    except ImportError:
        with open(fotograf_yolu, "rb") as f:
            raw = f.read()
            data = base64.standard_b64encode(raw).decode("utf-8")
        uzanti = Path(fotograf_yolu).suffix.lower()
        media_type = "image/jpeg" if uzanti in [".jpg", ".jpeg"] else "image/png"
        meta["kb"] = len(data) // 1024
        return data, media_type, meta


def _parse_json(metin: str) -> dict:
    metin = metin.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", metin, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    obj = re.search(r"\{.*\}", metin, re.DOTALL)
    if obj:
        return json.loads(obj.group())
    raise ValueError(f"JSON bulunamadı: {metin[:100]}")


def _parse_kg(deger) -> int:
    if deger is None:
        return 0
    try:
        f = float(str(deger).replace(",", "."))
        if f != int(f) and f < 1000:
            sonuc = int(round(f * 1000))
            logger.warning(f"⚠️ Ondalıklı kg düzeltildi: {deger} → {sonuc}")
            return sonuc
        return int(f)
    except (ValueError, TypeError):
        logger.error(f"kg parse hatası: {deger}")
        return 0


def _claude_fis_oku_once(fotograf_yolu: str, temperature: float = 0.0) -> FisVerisi:
    """Tek OCR denemesi."""
    foto_base64, media_type, foto_meta = _fotograf_to_base64(fotograf_yolu)

    # Düşük kalite fotoğraflarda DİKKATLİ OKU mesajı (paranoya yaratmadan)
    ek_uyari = ""
    if foto_meta.get("dusuk_kalite"):
        ek_uyari = (
            "\n\nNot: Bu fotoğraf nispeten düşük çözünürlüklü. "
            "Yine de tüm görebildiğin değerleri DİKKATLİCE oku ve doldur. "
            "Sadece tek karakter dahi göremediğin alanları null bırak."
        )

    response = _client.messages.create(
        model=OCR_MODEL,
        max_tokens=OCR_MAX_TOKENS,
        temperature=temperature,
        system=OCR_SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": foto_base64}
                },
                {"type": "text", "text": OCR_PROMPT + ek_uyari}
            ]
        }]
    )

    claude_cevap = response.content[0].text
    logger.info(f"OCR ham cevap: {claude_cevap}")

    veri = _parse_json(claude_cevap)

    # Kritik alanların tamamı null → fotoğraf okunamadı
    plaka_raw  = veri.get("plaka")
    tartim_raw = veri.get("net_tartim_kg")
    firma_raw  = veri.get("firma")
    if not plaka_raw and not tartim_raw and not firma_raw:
        raise ValueError(
            "Fişteki bilgiler okunamadı. Fotoğrafı daha net çekip tekrar deneyin."
        )

    net_tartim  = _parse_kg(veri.get("net_tartim_kg"))
    fire        = _parse_kg(veri.get("fire_kg"))
    net_agirlik = net_tartim - fire

    if net_agirlik < 0:
        logger.warning(f"⚠️ Net ağırlık negatif (tartım={net_tartim}, fire={fire}), fire sıfırlanıyor")
        net_agirlik = net_tartim
        fire = 0

    if net_agirlik == 0 or net_tartim == 0:
        raise ValueError("Fişteki ağırlık değeri okunamadı veya sıfır.")

    FIRE_KELIMELERI = {'fire', 'tenzil', 'fire kg', 'kayıp', 'kayip', 'hurda fire'}
    malzemeler_ham = veri.get("malzemeler") or {}
    malzemeler = {
        k: _parse_kg(v) for k, v in malzemeler_ham.items()
        if v and _parse_kg(v) > 0 and k.strip().lower() not in FIRE_KELIMELERI
    }
    # Malzeme toplamı net ağırlığı geçemez — geçerse net ağırlığa normalize et
    if malzemeler and net_agirlik > 0:
        toplam_malzeme = sum(malzemeler.values())
        if toplam_malzeme > net_agirlik:
            logger.warning(f"⚠️ Malzeme toplamı ({toplam_malzeme}) net ağırlığı ({net_agirlik}) geçiyor, normalize ediliyor")
            oran = net_agirlik / toplam_malzeme
            malzemeler = {k: max(1, int(v * oran)) for k, v in malzemeler.items()}

    ham_plaka = veri.get("plaka")
    temiz_plaka = re.sub(r'[\s\-]', '', ham_plaka).upper() if ham_plaka else None

    plaka_gecerli = bool(re.match(r'^\d{2}[A-Z]{1,3}\d{2,4}$', temiz_plaka)) if temiz_plaka else False
    if temiz_plaka and not plaka_gecerli:
        logger.warning(f"⚠️ GEÇERSİZ PLAKA FORMATI: '{temiz_plaka}'")

    plaka_uyari = None
    if temiz_plaka and not plaka_gecerli:
        plaka_uyari = f"⚠️ Plaka '{temiz_plaka}' geçersiz format — lütfen kontrol edin."

    logger.info(f"OCR sonuç — plaka={temiz_plaka} tartım={net_tartim}kg fire={fire}kg net={net_agirlik}kg firma={veri.get('firma')}")

    return FisVerisi(
        plaka            = temiz_plaka,
        fis_no           = str(veri.get("fis_no")) if veri.get("fis_no") else None,
        net_tartim_kg    = net_tartim,
        fire_kg          = fire,
        net_agirlik_kg   = net_agirlik,
        tarih            = veri.get("tarih"),
        firma            = veri.get("firma"),
        irsaliye_no      = veri.get("irsaliye_no"),
        kantar_no        = veri.get("kantar_no"),
        malzemeler       = malzemeler or None,
        claude_ham_cikti = claude_cevap,
        plaka_uyari      = plaka_uyari
    )


def claude_fis_oku(fotograf_yolu: str) -> FisVerisi:
    """
    OCR işlemini çalıştırır.
    Tüm denemeler temperature=0.0 ile yapılır (deterministik, halüsinasyon riskini azaltır).
    Tüm denemeler başarısız olursa hata döndürür.
    """
    logger.info(f"OCR başlıyor: {fotograf_yolu} | model={OCR_MODEL}")
    son_hata = None

    for deneme in range(OCR_MAX_RETRY):
        # Her zaman temperature=0.0 — halüsinasyon riskini en aza indirir.
        # Önceki kodda retry'da 0.2'ye çıkıyordu, bu YANLIŞ idi: daha yaratıcı
        # = daha çok uydurma. Şimdi düzeltildi.
        try:
            if deneme > 0:
                logger.info(f"OCR yeniden deneniyor ({deneme + 1}/{OCR_MAX_RETRY})")
            return _claude_fis_oku_once(fotograf_yolu, temperature=0.0)
        except Exception as e:
            son_hata = e
            logger.warning(f"OCR deneme {deneme + 1} başarısız: {e}")

    logger.error(f"OCR tüm denemeler başarısız: {son_hata}")
    return FisVerisi(
        hata=(
            f"Fiş okunamadı: {son_hata}. "
            "Fotoğrafı net ve aydınlık çekip tekrar deneyin."
        )
    )


# ── ASYNC WRAPPER — FastAPI event loop'unu bloklamaz ──────────────────

async def claude_fis_oku_async(fotograf_yolu: str) -> FisVerisi:
    """
    Senkron OCR işlemini thread pool'a taşır.
    FastAPI async endpoint'lerinden bu fonksiyon çağrılmalı.
    """
    return await asyncio.to_thread(claude_fis_oku, fotograf_yolu)


def fis_oku(fotograf_yolu: str) -> FisVerisi:
    """Senkron çağrı — background task veya senkron bağlamlar için."""
    fis = claude_fis_oku(fotograf_yolu)
    if fis.hata:
        logger.error(f"Fiş okunamadı: {fis.hata}")
    return fis


async def fis_oku_async(fotograf_yolu: str) -> FisVerisi:
    """Async çağrı — FastAPI endpoint'lerinden kullanılmalı."""
    return await claude_fis_oku_async(fotograf_yolu)


def fis_db_kaydet(fis: FisVerisi, foto_path: str, db) -> object:
    from app.core.database import WeighTicket, TicketStatus

    fis_tarihi = None
    if fis.tarih:
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                fis_tarihi = datetime.strptime(fis.tarih, fmt)
                break
            except ValueError:
                continue

    ticket = WeighTicket(
        foto_path     = foto_path,
        plaka         = fis.plaka,
        fis_no        = fis.fis_no,
        agirlik_kg    = fis.net_agirlik_kg,
        net_tartim_kg = fis.net_tartim_kg,
        fire_kg       = fis.fire_kg or 0,
        firma         = fis.firma,
        fis_tarihi    = fis_tarihi,
        malzeme       = "hurda",
        malzemeler    = json.dumps(fis.malzemeler, ensure_ascii=False) if fis.malzemeler else None,
        ocr_ham_cikti = fis.claude_ham_cikti,
        status        = TicketStatus.OCR_TAMAMLANDI if not fis.hata else TicketStatus.HATA,
        hata_mesaji   = fis.hata
    )

    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    logger.info(
        f"✅ Fiş kaydedildi: id={ticket.id} plaka={ticket.plaka} "
        f"firma={ticket.firma} fis_no={ticket.fis_no} agirlik={ticket.agirlik_kg}kg"
    )
    return ticket