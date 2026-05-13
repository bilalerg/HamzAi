# migrations.py — Startup Migration ve Recovery
#
# main.py'de inline SQL migration vardı — bu dosyaya taşındı.
# İleride Alembic'e geçişte sadece bu dosyayı değiştirmek yeterli.

from app.core.logger import logger


def calistir(engine, db):
    """
    Uygulama başlangıcında çalışır.
    1. Eksik kolonları ekler (ADD COLUMN IF NOT EXISTS)
    2. Eksik tabloları oluşturur
    3. Yarım kalan işlemleri tespit eder

    Senkron fonksiyon — main.py'de asyncio.to_thread ile çağrılır.
    """
    from sqlalchemy import text

    with engine.connect() as conn:

        # ── KOLON MİGRATIONLARI ───────────────────────────────────────
        _kolon_ekle(conn, text(
            "ALTER TABLE waybills ADD COLUMN IF NOT EXISTS malzemeler_json TEXT"
        ), "malzemeler_json (waybills)")

        _kolon_ekle(conn, text(
            "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS tam_gecmis TEXT"
        ), "tam_gecmis (chat_sessions)")

        for kolon, sql_tip in [
            ("username", "VARCHAR(100)"),
            ("profaix_user_id", "VARCHAR(100)"),
            ("auth_session_id", "VARCHAR(100)"),
            ("ip_adresi", "VARCHAR(50)"),
        ]:
            _kolon_ekle(conn, text(
                f"ALTER TABLE islem_logs ADD COLUMN IF NOT EXISTS {kolon} {sql_tip}"
            ), f"{kolon} (islem_logs)")

        # ── TABLO MİGRATIONLARI ───────────────────────────────────────
        _tablo_olustur(conn, text("""
            CREATE TABLE IF NOT EXISTS islem_logs (
                id SERIAL PRIMARY KEY,
                islem_tipi VARCHAR(50) NOT NULL,
                cari_adi VARCHAR(200),
                tutar BIGINT,
                fatura_id VARCHAR(100),
                irsaliye_id VARCHAR(100),
                plaka VARCHAR(20),
                aciklama TEXT,
                session_id VARCHAR(100),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """), "islem_logs")

        _tablo_olustur(conn, text("""
            CREATE TABLE IF NOT EXISTS active_logins (
                id SERIAL PRIMARY KEY,
                principal_key VARCHAR(150) UNIQUE NOT NULL,
                session_id VARCHAR(100) NOT NULL,
                username VARCHAR(100),
                user_agent TEXT,
                ip_adresi VARCHAR(50),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """), "active_logins")

        _tablo_olustur(conn, text("""
            CREATE TABLE IF NOT EXISTS billable_events (
                id SERIAL PRIMARY KEY,
                event_id VARCHAR(100) UNIQUE NOT NULL,
                event_key VARCHAR(200) UNIQUE,
                user_id INTEGER,
                username VARCHAR(100),
                profaix_user_id VARCHAR(100),
                event_type VARCHAR(50) NOT NULL,
                amount INTEGER NOT NULL DEFAULT 1,
                source VARCHAR(50),
                description TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """), "billable_events")

    # ── YARIMA KALMIŞ İŞLEM TESPİTİ ──────────────────────────────────
    from app.core.database import WeighTicket, TicketStatus
    bekleyenler = db.query(WeighTicket).filter(
        WeighTicket.status == TicketStatus.IRSALIYE_OLUSTURULUYOR
    ).all()
    if bekleyenler:
        logger.warning(f"⚠️ {len(bekleyenler)} yarım kalan işlem tespit edildi (IRSALIYE_OLUSTURULUYOR)")
    else:
        logger.info("✅ Yarım kalan işlem yok")


def _kolon_ekle(conn, stmt, isim: str):
    try:
        conn.execute(stmt)
        conn.commit()
        logger.info(f"✅ Migration: {isim} kolonu hazır")
    except Exception as e:
        logger.warning(f"Migration atlandı ({isim}): {e}")


def _tablo_olustur(conn, stmt, isim: str):
    try:
        conn.execute(stmt)
        conn.commit()
        logger.info(f"✅ Migration: {isim} tablosu hazır")
    except Exception as e:
        logger.warning(f"Migration atlandı ({isim}): {e}")