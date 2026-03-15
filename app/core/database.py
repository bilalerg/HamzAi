# database.py — Veritabanı Bağlantısı ve Tablo Modelleri
#
# NASIL ÇALIŞIR?
# SQLAlchemy iki katmandan oluşur:
# 1. Engine  → Veritabanına fiziksel bağlantı (PostgreSQL'e köprü)
# 2. Session → Her işlem için açılan oturum (sorgu, kayıt, silme)
#
# Tablolar "Model" olarak tanımlanır — her Python sınıfı bir DB tablosuna karşılık gelir.

from sqlalchemy import (
    create_engine, Column, String, Integer,
    DateTime, Text, Enum, ForeignKey, BigInteger
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func
import enum
from app.core.config import DATABASE_URL
from app.core.logger import logger

# ── ENGINE ──────────────────────────────────────────────────────────────
# Engine veritabanı bağlantısını yönetir.
# pool_pre_ping=True → Her sorguda bağlantının hâlâ açık olduğunu kontrol eder.
# 7/24 çalışan sistemlerde bağlantı zaman aşımına uğrayabilir — bu ayar bunu önler.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    echo=False  # True yaparsanız her SQL sorgusunu terminale yazar — debug için kullanışlı
)

# ── SESSION ─────────────────────────────────────────────────────────────
# Her veritabanı işlemi bir "session" içinde yapılır.
# Session işlemi ya tamamen yapar (commit) ya da hiç yapmaz (rollback).
# Buna ACID garantisi denir — yarım kalan işlem olmaz.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ── BASE ─────────────────────────────────────────────────────────────────
# Tüm model sınıfları Base'den türer.
# Base hangi sınıfların tablo olduğunu takip eder.
Base = declarative_base()


# ── STATUS KODLARI ───────────────────────────────────────────────────────
# Python Enum'u — sadece belirli değerlere izin verir.
# Veritabanına yanlış status yazılmasını engeller.
# Örnek: status = "TAMAM" yazamazsın, sadece tanımlı değerler geçer.
class TicketStatus(str, enum.Enum):
    ALINDI = "ALINDI"
    OCR_TAMAMLANDI = "OCR_TAMAMLANDI"
    OCR_DOGRULAMA_BEKLIYOR = "OCR_DOGRULAMA_BEKLIYOR"
    PLAKA_KONTROL_BEKLENIYOR = "PLAKA_KONTROL_BEKLENIYOR"
    ZIRVE_BEKLENIYOR = "ZIRVE_BEKLENIYOR"
    ZIRVE_DEVAM_EDIYOR = "ZIRVE_DEVAM_EDIYOR"
    ZIRVE_YARIDA_KALDI = "ZIRVE_YARIDA_KALDI"
    ZIRVE_TAMAMLANDI = "ZIRVE_TAMAMLANDI"
    FABRIKA_BEKLENIYOR = "FABRIKA_BEKLENIYOR"
    FABRIKA_ITIRAZI = "FABRIKA_ITIRAZI"
    FATURA_KESILIYOR = "FATURA_KESILIYOR"
    TAMAMLANDI = "TAMAMLANDI"
    IPTAL = "IPTAL"
    HATA = "HATA"


# ── TABLO 1: VEHICLES ────────────────────────────────────────────────────
# Araç (plaka) kayıtları
# Her plaka bir kez kaydedilir, sonraki işlemlerde buraya bakılır.
class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plaka = Column(String(20), unique=True, nullable=False, index=True)
    # index=True → Bu kolona göre sık sık arama yapacağız, index hızlandırır

    aktif = Column(Integer, default=1)  # 1: aktif, 0: pasif
    notlar = Column(Text, nullable=True)

    # Zaman damgaları — server_default ile DB tarafında otomatik dolar
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # İlişki — bu araçla ilgili tüm fişlere ulaşmak için
    tickets = relationship("WeighTicket", back_populates="vehicle")

    def __repr__(self):
        return f"<Vehicle plaka={self.plaka}>"


# ── TABLO 2: WEIGH_TICKETS ────────────────────────────────────────────────
# Kantar fişi verileri
# Her kantar fişi buraya kaydedilir. OCR çıktısı da ham haliyle saklanır.
class WeighTicket(Base):
    __tablename__ = "weigh_tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Fotoğraf bilgisi
    foto_path = Column(String(500), nullable=True)  # fotoğraf dosya yolu

    # OCR çıktısı
    plaka = Column(String(20), nullable=True, index=True)
    agirlik_kg = Column(Integer, nullable=True)  # kg cinsinden
    malzeme = Column(String(100), nullable=True)
    fis_tarihi = Column(DateTime, nullable=True)
    ocr_ham_cikti = Column(Text, nullable=True)  # Claude'un raw JSON çıktısı

    # Çapraz kontrol
    tesseract_agirlik = Column(Integer, nullable=True)  # Tesseract'ın okuduğu ağırlık
    ocr_uyusma = Column(Integer, nullable=True)  # 1: uyuştu, 0: uyuşmadı

    # Durum takibi — roadmap'teki 14 status kodu
    status = Column(
        Enum(TicketStatus),
        default=TicketStatus.ALINDI,
        nullable=False
    )
    hata_mesaji = Column(Text, nullable=True)  # hata varsa buraya yazılır

    # İlişkiler
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)
    vehicle = relationship("Vehicle", back_populates="tickets")
    waybill = relationship("Waybill", back_populates="ticket", uselist=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<WeighTicket id={self.id} plaka={self.plaka} status={self.status}>"


# ── TABLO 3: WAYBILLS ─────────────────────────────────────────────────────
# İrsaliye kayıtları
# Zirve'de oluşturulan her irsaliye buraya kaydedilir.
class Waybill(Base):
    __tablename__ = "waybills"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # İrsaliye bilgileri
    irsaliye_no = Column(String(50), nullable=True, unique=True)
    ref_kodu = Column(String(20), nullable=True, unique=True, index=True)
    # ref_kodu → "HZ-1024" formatında — fabrika mail eşleştirme için kritik

    # Fabrika mail bilgisi
    fabrika_mail = Column(String(200), nullable=True)
    mail_gonderildi_at = Column(DateTime, nullable=True)
    teyit_geldi_at = Column(DateTime, nullable=True)
    teyit_mail_icerik = Column(Text, nullable=True)  # fabrika mailinin tam içeriği
    nlp_karar = Column(String(20), nullable=True)  # "ONAY" veya "ITIRAZ"

    # Durum
    status = Column(Enum(TicketStatus), default=TicketStatus.ZIRVE_BEKLENIYOR)

    # İlişkiler
    ticket_id = Column(Integer, ForeignKey("weigh_tickets.id"))
    ticket = relationship("WeighTicket", back_populates="waybill")
    invoice = relationship("Invoice", back_populates="waybill", uselist=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Waybill irsaliye_no={self.irsaliye_no} ref={self.ref_kodu}>"


# ── TABLO 4: INVOICES ─────────────────────────────────────────────────────
# Fatura kayıtları
# Paraşüt'te kesilen her fatura buraya kaydedilir.
class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)

    fatura_no = Column(String(50), nullable=True, unique=True)
    tutar = Column(BigInteger, nullable=True)  # kuruş cinsinden (float hatası önlemek için)
    parasut_id = Column(String(100), nullable=True)  # Paraşüt'teki ID
    parasut_response = Column(Text, nullable=True)  # API yanıtının ham hali

    status = Column(Enum(TicketStatus), default=TicketStatus.FATURA_KESILIYOR)

    waybill_id = Column(Integer, ForeignKey("waybills.id"))
    waybill = relationship("Waybill", back_populates="invoice")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Invoice fatura_no={self.fatura_no} tutar={self.tutar}>"


# ── TABLO 5: ALERTS ───────────────────────────────────────────────────────
# Uyarı ve kontrol mesajları
# Muhasebeciye gönderilen her uyarı buraya kaydedilir.
class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)

    tur = Column(String(50), nullable=False)
    # "YENI_PLAKA", "DUPLICATE", "OCR_UYUSMAZLIK", "FABRIKA_ITIRAZ"

    mesaj = Column(Text, nullable=False)
    ticket_id = Column(Integer, ForeignKey("weigh_tickets.id"), nullable=True)

    gonderildi_mi = Column(Integer, default=0)  # 0: hayır, 1: evet
    cevap = Column(Text, nullable=True)  # muhasebecinin cevabı

    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<Alert tur={self.tur} gonderildi={self.gonderildi_mi}>"


# ── TABLO 6: SYSTEM_LOGS ──────────────────────────────────────────────────
# Her işlemin kaydı
# Loguru dosyaya yazıyor, bu tablo ise DB'de tutuyor — sorgulama için kullanışlı.
class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    seviye = Column(String(10), nullable=False)  # INFO, WARNING, ERROR
    modul = Column(String(50), nullable=True)  # "ocr", "zirve", "mail"
    mesaj = Column(Text, nullable=False)
    ticket_id = Column(Integer, ForeignKey("weigh_tickets.id"), nullable=True)
    sure_ms = Column(Integer, nullable=True)  # işlem kaç ms sürdü

    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<SystemLog seviye={self.seviye} modul={self.modul}>"


# ── VERİTABANI BAŞLATMA ───────────────────────────────────────────────────
def init_db():
    """
    Tüm tabloları veritabanında oluşturur.

    NEDEN Base.metadata.create_all()?
    Base tüm modelleri takip ediyor. create_all() her modele bakıp
    tablonun DB'de var olup olmadığını kontrol eder.
    Yoksa CREATE TABLE çalıştırır, varsa dokunmaz — güvenli.
    """
    logger.info("Veritabanı tabloları oluşturuluyor...")
    Base.metadata.create_all(bind=engine)
    logger.info("✅ Tüm tablolar hazır")


def get_db():
    """
    Her işlem için yeni bir session açar.

    NEDEN yield?
    Bu bir "context manager" — session'ı açar, işi bitince kapatır.
    Kapatmazsan bağlantı havuzu dolar, sistem yavaşlar.
    try/finally garantisi: hata olsa bile session kapatılır.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()