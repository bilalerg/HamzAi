import sys
sys.path.insert(0, '.')
from app.core.database import engine
from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text("ALTER TYPE ticketstatus ADD VALUE IF NOT EXISTS 'IRSALIYE_OLUSTURULUYOR'"))
    conn.execute(text("ALTER TYPE ticketstatus ADD VALUE IF NOT EXISTS 'IRSALIYE_TAMAMLANDI'"))
    conn.commit()
    print('✅ Enum güncellendi!')