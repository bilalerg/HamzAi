# plaka_kontrol.py — Plaka Kontrol Sistemi
#
# SORUMLULUK:
# Gelen plakanın daha önce görülüp görülmediğini kontrol eder.
# 3 sonuç döndürür: YENİ, NORMAL, DUPLICATE
#
# NEDEN AYRI DOSYA?
# OCR okuma (reader.py) ve plaka kontrolü farklı işler.
# Tek sorumluluk prensibi — her dosya bir işi yapar.
# Yarın plaka kontrol mantığı değişirse sadece bu dosyaya dokunuruz.

from datetime import datetime, date
from enum import Enum
from app.core.database import Vehicle, WeighTicket, Alert, TicketStatus
from app.core.logger import logger


# ── SONUÇ TİPLERİ ───────────────────────────────────────────────────────
# Enum kullanıyoruz çünkü sadece 3 sonuç olabilir.
# "yeni" veya "YENİ" veya "Yeni" gibi yazım hatalarını önler.
# Kod her yerde PlakaSonuc.YENI der — karışıklık olmaz.
class PlakaSonuc(Enum):
    YENI = "YENI"  # İlk kez görülen plaka
    NORMAL = "NORMAL"  # Tanıdık plaka, bugün ilk kez
    DUPLICATE = "DUPLICATE"  # Bugün daha önce gelmiş


# ── YARDIMCI: UYARI OLUŞTUR ─────────────────────────────────────────────
def uyari_olustur(tur: str, mesaj: str, ticket_id: int, db) -> Alert:
    """
    Alerts tablosuna yeni uyarı yazar.

    NEDEN BU FONKSİYON?
    WP entegrasyonu henüz yok (Faz 5'te gelecek).
    Ama uyarıları şimdiden DB'ye yazıyoruz.
    Faz 5'te WP bağlanınca bu tablodan okuyup mesaj atacak.
    Yani WP olmasa bile uyarılar kaybolmuyor.

    tur örnekleri: "YENI_PLAKA", "DUPLICATE", "OCR_UYUSMAZLIK"
    """
    alert = Alert(
        tur=tur,
        mesaj=mesaj,
        ticket_id=ticket_id,
        gonderildi_mi=0  # Henüz gönderilmedi — WP gelince 1 olacak
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)

    logger.info(f"🔔 Uyarı oluşturuldu: tur={tur} ticket_id={ticket_id}")
    return alert


# ── ANA FONKSİYON: PLAKA KONTROL ────────────────────────────────────────
def plaka_kontrol_et(plaka: str, ticket_id: int, db) -> PlakaSonuc:
    """
    Plakanın durumunu kontrol eder ve gerekli işlemleri yapar.

    ADIMLAR:
    1. vehicles tablosunda plaka var mı? → Yeni mi değil mi?
    2. Yeni ise → vehicles'a ekle + uyarı oluştur
    3. Değil ise → bugün daha önce işlem gördü mü?
    4. Duplicate ise → uyarı oluştur
    5. Sonucu döndür

    NEDEN ticket_id ALIYORUZ?
    Uyarı oluştururken hangi fişe ait olduğunu bilmemiz lazım.
    Alerts tablosunda ticket_id kolonu var — oraya yazıyoruz.
    """

    if not plaka:
        logger.warning("Plaka boş geldi, kontrol atlanıyor")
        return PlakaSonuc.NORMAL

    # Plakaları büyük harfe çevir — "34abc123" ve "34ABC123" aynı plaka
    plaka = plaka.upper().strip()

    # ── ADIM 1: Bu plaka daha önce görülmüş mü? ─────────────────────────
    # vehicles tablosunda plaka kolonu unique — aynı plaka iki kez girilemiyor
    # filter() → WHERE plaka = '34ABC123' demek
    # first() → ilk sonucu al, yoksa None döndür
    mevcut_arac = db.query(Vehicle).filter(
        Vehicle.plaka == plaka
    ).first()

    # ── ADIM 2: YENİ PLAKA ──────────────────────────────────────────────
    if not mevcut_arac:
        logger.info(f"🆕 Yeni plaka tespit edildi: {plaka}")

        # vehicles tablosuna ekle
        yeni_arac = Vehicle(plaka=plaka, aktif=1)
        db.add(yeni_arac)
        db.commit()
        db.refresh(yeni_arac)

        # Uyarı oluştur — Faz 5'te WP'den gönderilecek
        uyari_olustur(
            tur="YENI_PLAKA",
            mesaj=f"Yeni araç kaydedildi: {plaka}",
            ticket_id=ticket_id,
            db=db
        )

        return PlakaSonuc.YENI

    # ── ADIM 3: BUGÜN DAHA ÖNCE GELDİ Mİ? ──────────────────────────────
    # weigh_tickets tablosunda bugün bu plakadan işlem var mı?
    #
    # NEDEN BU SORGU?
    # Aynı tırın günde 2 kez gelmesi şüpheli.
    # İş kuralı: günde max 1 kantar fişi / araç
    #
    # date.today() → bugünün tarihi (2026-03-17)
    # WeighTicket.created_at >= bugünün başlangıcı
    # WeighTicket.created_at <  yarının başlangıcı
    bugun_baslangic = datetime.combine(date.today(), datetime.min.time())

    bugunki_islem = db.query(WeighTicket).filter(
        WeighTicket.plaka == plaka,
        WeighTicket.created_at >= bugun_baslangic,
        WeighTicket.id != ticket_id  # Kendisini sayma!
    ).first()

    # ── ADIM 4: DUPLICATE ───────────────────────────────────────────────
    if bugunki_islem:
        logger.warning(f"⚠️ Duplicate tespit edildi: {plaka} bugün daha önce gelmiş (ticket_id={bugunki_islem.id})")

        # Uyarı oluştur
        uyari_olustur(
            tur="DUPLICATE",
            mesaj=f"Bu araç bugün daha önce geldi: {plaka} (önceki işlem ID: {bugunki_islem.id})",
            ticket_id=ticket_id,
            db=db
        )

        # Status güncelle — muhasebeci onayı bekleniyor
        # NEDEN STATUS DEĞİŞİYOR?
        # Duplicate durumunda sistem otomatik devam etmemeli.
        # Status PLAKA_KONTROL_BEKLENIYOR olursa state recovery
        # sistemi bunu görür ve muhasebeci onayı bekler.
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()
        if ticket:
            ticket.status = TicketStatus.PLAKA_KONTROL_BEKLENIYOR
            db.commit()

        return PlakaSonuc.DUPLICATE

    # ── ADIM 5: NORMAL AKIŞ ─────────────────────────────────────────────
    logger.info(f"✅ Plaka kontrolü geçti: {plaka} — normal akış")
    return PlakaSonuc.NORMAL