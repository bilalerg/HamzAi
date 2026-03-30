# database.py — Veritabanı Bağlantısı ve Tablo Modelleri

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

# Engine — veritabanına fiziksel bağlantı
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    echo=False
)

# Session — her işlem için açılan oturum
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base — tüm modeller buradan türer
Base = declarative_base()


# ── STATUS KODLARI ───────────────────────────────────────────────────────
class TicketStatus(str, enum.Enum):
    ALINDI                    = "ALINDI"
    OCR_TAMAMLANDI            = "OCR_TAMAMLANDI"
    OCR_DOGRULAMA_BEKLIYOR    = "OCR_DOGRULAMA_BEKLIYOR"
    PLAKA_KONTROL_BEKLENIYOR  = "PLAKA_KONTROL_BEKLENIYOR"
    IRSALIYE_OLUSTURULUYOR    = "IRSALIYE_OLUSTURULUYOR"
    IRSALIYE_TAMAMLANDI       = "IRSALIYE_TAMAMLANDI"
    FABRIKA_BEKLENIYOR        = "FABRIKA_BEKLENIYOR"
    FABRIKA_ITIRAZI           = "FABRIKA_ITIRAZI"
    FATURA_KESILIYOR          = "FATURA_KESILIYOR"
    TAMAMLANDI                = "TAMAMLANDI"
    IPTAL                     = "IPTAL"
    HATA                      = "HATA"


# ── TABLO 1: VEHICLES ────────────────────────────────────────────────────
class Vehicle(Base):
    __tablename__ = "vehicles"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    plaka      = Column(String(20), unique=True, nullable=False, index=True)
    aktif      = Column(Integer, default=1)
    notlar     = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    tickets = relationship("WeighTicket", back_populates="vehicle")

    def __repr__(self):
        return f"<Vehicle plaka={self.plaka}>"


# ── TABLO 2: WEIGH_TICKETS ────────────────────────────────────────────────
class WeighTicket(Base):
    __tablename__ = "weigh_tickets"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    foto_path        = Column(String(500), nullable=True)
    plaka            = Column(String(20), nullable=True, index=True)
    fis_no           = Column(String(50), nullable=True, index=True)
    agirlik_kg       = Column(Integer, nullable=True)
    malzeme          = Column(String(100), nullable=True)
    fis_tarihi       = Column(DateTime, nullable=True)
    ocr_ham_cikti    = Column(Text, nullable=True)
    tesseract_agirlik = Column(Integer, nullable=True)
    ocr_uyusma       = Column(Integer, nullable=True)
    status           = Column(Enum(TicketStatus), default=TicketStatus.ALINDI, nullable=False)
    hata_mesaji      = Column(Text, nullable=True)
    vehicle_id       = Column(Integer, ForeignKey("vehicles.id"), nullable=True)
    created_at       = Column(DateTime, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())

    vehicle = relationship("Vehicle", back_populates="tickets")
    waybill = relationship("Waybill", back_populates="ticket", uselist=False)

    def __repr__(self):
        return f"<WeighTicket id={self.id} plaka={self.plaka} status={self.status}>"


# ── TABLO 3: WAYBILLS ─────────────────────────────────────────────────────
class Waybill(Base):
    __tablename__ = "waybills"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    irsaliye_no        = Column(String(50), nullable=True, unique=True)
    ref_kodu           = Column(String(20), nullable=True, unique=True, index=True)
    fabrika_mail       = Column(String(200), nullable=True)
    mail_gonderildi_at = Column(DateTime, nullable=True)
    teyit_geldi_at     = Column(DateTime, nullable=True)
    teyit_mail_icerik  = Column(Text, nullable=True)
    nlp_karar          = Column(String(20), nullable=True)
    parasut_irsaliye_id = Column(String(100), nullable=True)
    status             = Column(Enum(TicketStatus), default=TicketStatus.IRSALIYE_OLUSTURULUYOR)
    ticket_id          = Column(Integer, ForeignKey("weigh_tickets.id"))
    created_at         = Column(DateTime, server_default=func.now())
    updated_at         = Column(DateTime, server_default=func.now(), onupdate=func.now())

    ticket  = relationship("WeighTicket", back_populates="waybill")
    invoice = relationship("Invoice", back_populates="waybill", uselist=False)

    def __repr__(self):
        return f"<Waybill irsaliye_no={self.irsaliye_no} ref={self.ref_kodu}>"


# ── TABLO 4: INVOICES ─────────────────────────────────────────────────────
class Invoice(Base):
    __tablename__ = "invoices"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    fatura_no        = Column(String(50), nullable=True, unique=True)
    tutar            = Column(BigInteger, nullable=True)
    parasut_id       = Column(String(100), nullable=True)
    parasut_response = Column(Text, nullable=True)
    status           = Column(Enum(TicketStatus), default=TicketStatus.FATURA_KESILIYOR)
    waybill_id       = Column(Integer, ForeignKey("waybills.id"))
    created_at       = Column(DateTime, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())

    waybill = relationship("Waybill", back_populates="invoice")

    def __repr__(self):
        return f"<Invoice fatura_no={self.fatura_no} tutar={self.tutar}>"


# ── TABLO 5: ALERTS ───────────────────────────────────────────────────────
class Alert(Base):
    __tablename__ = "alerts"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    tur          = Column(String(50), nullable=False)
    mesaj        = Column(Text, nullable=False)
    ticket_id    = Column(Integer, ForeignKey("weigh_tickets.id"), nullable=True)
    gonderildi_mi = Column(Integer, default=0)
    cevap        = Column(Text, nullable=True)
    created_at   = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<Alert tur={self.tur} gonderildi={self.gonderildi_mi}>"


# ── TABLO 6: SYSTEM_LOGS ──────────────────────────────────────────────────
class SystemLog(Base):
    __tablename__ = "system_logs"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    seviye    = Column(String(10), nullable=False)
    modul     = Column(String(50), nullable=True)
    mesaj     = Column(Text, nullable=False)
    ticket_id = Column(Integer, ForeignKey("weigh_tickets.id"), nullable=True)
    sure_ms   = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<SystemLog seviye={self.seviye} modul={self.modul}>"


# ── TABLO 7: PROCESSED_MAILS ─────────────────────────────────────────────
# Restart sonrası eski maillerin tekrar işlenmesini önler.
# Listener her işlediği mailin ID'sini buraya kaydeder.
# Sonraki çalışmada bu ID'ler DB'den yüklenir, tekrar işlenmez.
class ProcessedMail(Base):
    __tablename__ = "processed_mails"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    mail_id    = Column(String(200), unique=True, nullable=False, index=True)
    ref_kodu   = Column(String(20), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<ProcessedMail mail_id={self.mail_id} ref={self.ref_kodu}>"


# ── TABLO 8: CHAT_SESSIONS ────────────────────────────────────────────────
# Web ve WhatsApp chatbot session'larını DB'de tutar.
# Restart sonrası kullanıcı ismi ve konuşma geçmişi kaybolmaz.
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), unique=True, nullable=False, index=True)
    isim       = Column(String(100), nullable=True)
    gecmis     = Column(Text, nullable=True)  # JSON string olarak saklanır
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<ChatSession session_id={self.session_id} isim={self.isim}>"


# ── VERİTABANI BAŞLATMA ───────────────────────────────────────────────────
def init_db():
    logger.info("Veritabanı tabloları oluşturuluyor...")
    Base.metadata.create_all(bind=engine)
    logger.info("✅ Tüm tablolar hazır")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()