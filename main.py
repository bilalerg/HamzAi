from app.core.config import validate_config
from app.core.logger import logger
from app.core.database import init_db

if __name__ == "__main__":
    validate_config()
    logger.info("HamzaAI başlatılıyor...")
    init_db()
    logger.info("✅ Sistem hazır")