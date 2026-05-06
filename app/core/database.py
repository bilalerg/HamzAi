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

engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


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


class Vehicle(Base):
    __tablename__ = "vehicles"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    plaka      = Column(String(20), unique=True, nullable=False, index=True)
    aktif      = Column(Integer, default=1)
    notlar     = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    tickets = relationship("WeighTicket", back_populates="vehicle")
    def __repr__(self): return f"<Vehicle plaka={self.plaka}>"


class WeighTicket(Base):
    __tablename__ = "weigh_tickets"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    foto_path        = Column(String(500), nullable=True)
    plaka            = Column(String(20), nullable=True, index=True)
    fis_no           = Column(String(50), nullable=True, index=True)
    agirlik_kg       = Column(Integer, nullable=True)
    net_tartim_kg    = Column(Integer, nullable=True)
    fire_kg          = Column(Integer, default=0, nullable=True)
    malzeme          = Column(String(100), nullable=True)
    firma            = Column(String(200), nullable=True)
    malzemeler       = Column(Text, nullable=True)  # JSON: {"BONUS": 7700, "KARMA": 6000}
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
    def __repr__(self): return f"<WeighTicket id={self.id} plaka={self.plaka} status={self.status}>"


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
    malzemeler_json    = Column(Text, nullable=True)
    status             = Column(Enum(TicketStatus), default=TicketStatus.IRSALIYE_OLUSTURULUYOR)
    ticket_id          = Column(Integer, ForeignKey("weigh_tickets.id"))
    created_at         = Column(DateTime, server_default=func.now())
    updated_at         = Column(DateTime, server_default=func.now(), onupdate=func.now())
    ticket  = relationship("WeighTicket", back_populates="waybill")
    invoice = relationship("Invoice", back_populates="waybill", uselist=False)
    def __repr__(self): return f"<Waybill irsaliye_no={self.irsaliye_no}>"


class Invoice(Base):
    __tablename__ = "invoices"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    fatura_no        = Column(String(50), nullable=True, unique=True)
    tutar            = Column(BigInteger, nullable=True)
    birim_fiyat      = Column(Integer, nullable=True)
    malzeme_fiyatlari = Column(Text, nullable=True)  # JSON: {"BONUS": 16, "KARMA": 14}
    parasut_id       = Column(String(100), nullable=True)
    parasut_response = Column(Text, nullable=True)
    status           = Column(Enum(TicketStatus), default=TicketStatus.FATURA_KESILIYOR)
    waybill_id       = Column(Integer, ForeignKey("waybills.id"))
    created_at       = Column(DateTime, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())
    waybill = relationship("Waybill", back_populates="invoice")
    def __repr__(self): return f"<Invoice fatura_no={self.fatura_no} tutar={self.tutar}>"


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), unique=True, nullable=False, index=True)
    isim       = Column(String(100), nullable=True)
    gecmis     = Column(Text, nullable=True)
    tam_gecmis = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())



class IslemLog(Base):
    __tablename__ = "islem_logs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    islem_tipi  = Column(String(50), nullable=False, index=True)
    cari_adi    = Column(String(200), nullable=True, index=True)
    tutar       = Column(BigInteger, nullable=True)
    fatura_id   = Column(String(100), nullable=True)
    irsaliye_id = Column(String(100), nullable=True)
    plaka       = Column(String(20), nullable=True)
    aciklama    = Column(Text, nullable=True)
    session_id  = Column(String(100), nullable=True)
    created_at  = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<IslemLog tip={self.islem_tipi} cari={self.cari_adi}>"


class User(Base):
    """
    Kullanıcı tablosu.
    - username: giriş adı (benzersiz)
    - password_hash: bcrypt ile hashlenmiş şifre (düz metin asla saklanmaz)
    - rol: "admin" veya "staff" — ileride yetki ayrımı için
    - aktif: 0 yapılırsa kullanıcı giriş yapamaz (silmeden devre dışı bırakma)
    - son_giris: son giriş zamanı — audit için
    """
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    ad_soyad      = Column(String(100), nullable=True)
    rol           = Column(String(20), default="staff", nullable=False)  # admin / staff
    aktif         = Column(Integer, default=1)
    son_giris     = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, server_default=func.now())
    def __repr__(self): return f"<User username={self.username} rol={self.rol}>"


class AuditLog(Base):
    """
    Denetim kaydı — kim, ne zaman, ne yaptı.
    Her intent tetiklendiğinde buraya yazılır.
    Örnek: bilal | 2026-05-04 15:32 | bekleyen_irsaliyeler | params: {}
    """
    __tablename__ = "audit_logs"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True)
    username   = Column(String(50), nullable=True)  # join yapmadan hızlı okuma için
    intent     = Column(String(50), nullable=True)
    params     = Column(Text, nullable=True)   # JSON
    ip_adresi  = Column(String(50), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<AuditLog user={self.username} intent={self.intent}>"


class UsageLog(Base):
    """
    Kullanım sayacı — faturalama için.
    Her ücretli işlemde buraya yazılır.
    Hata mesajları ucretli=0 olarak kaydedilir.
    """
    __tablename__ = "usage_logs"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=True)
    username         = Column(String(50), nullable=True, index=True)
    profaix_user_id  = Column(String(100), nullable=True, index=True)  # profaix.com UUID
    islem_turu       = Column(String(50), nullable=False, index=True)
    # İşlem tipleri:
    # FIS_OCR       — fiş yükleme ve OCR işlemi
    # IRSALIYE      — irsaliye oluşturma
    # FATURA        — tek fatura kesme
    # TOPLU_FATURA  — toplu fatura kesme
    # TAHSILAT      — tahsilat ekleme
    # CARI          — yeni cari oluşturma
    # CHAT          — her chat mesajı (sohbet dahil)
    # SORGU         — fatura/bakiye/hesap özeti sorguları
    aciklama    = Column(Text, nullable=True)      # kısa özet
    ucretli     = Column(Integer, default=1)       # 1=ücretli, 0=hata/iptal
    created_at  = Column(DateTime, server_default=func.now(), index=True)
    def __repr__(self): return f"<UsageLog user={self.username} islem={self.islem_turu} ucretli={self.ucretli}>"


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