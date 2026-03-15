from app.core.config import validate_config
from app.core.logger import logger

if __name__ == "__main__":
    validate_config()

    logger.info("HamzaAI başlatılıyor...")
    logger.debug("Ortam: development — tüm debug logları görünür")
    logger.warning("Bu bir uyarı mesajı örneği")
    logger.error("Bu bir hata mesajı örneği")
    logger.info("✅ Sistem hazır")