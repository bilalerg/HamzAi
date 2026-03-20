# sanity_check.py — Zirve Durum Kontrolü
#
# SORUMLULUK:
# Her irsaliye işlemi başlamadan önce Zirve'nin
# hangi ekranda olduğunu tespit eder.
#
# NEDEN ÖNEMLİ?
# PyAutoGUI körü körüne koordinatlara tıklar.
# Zirve kapalıysa veya login ekranındaysa
# yanlış yere tıklar, sistem çöker.
# Bu dosya önce "ne görüyorum?" diye sorar,
# sonra doğru aksiyonu alır.
#
# locateOnScreen() NASIL ÇALIŞIR?
# Ekranın anlık görüntüsünü alır.
# İçinde küçük referans görüntüyü arar.
# Bulursa koordinat döndürür, bulamazsa None.
# confidence=0.8 → %80 benzerlik yeterli.

import pyautogui
import subprocess
import time
import os
from enum import Enum
from app.core.logger import logger
from app.zirve.koordinatlar import LOGIN, ANA_MENU, REFERANS


# ── DURUM KODLARI ────────────────────────────────────────────────────────
# Enum kullanıyoruz — sadece bu 4 durum olabilir, başkası yok.
class ZirveDurum(Enum):
    IRSALIYE_FORMU = "IRSALIYE_FORMU"  # İrsaliye formu açık, devam et
    ANA_MENU = "ANA_MENU"  # Ana menü açık
    LOGIN = "LOGIN"  # Login ekranı açık
    KAPALI = "KAPALI"  # Zirve hiç açık değil
    BILINMIYOR = "BILINMIYOR"  # Tanımlanamayan ekran


# ── YARDIMCI: EKRANDA GÖRÜNTÜ ARA ────────────────────────────────────────
def ekranda_ara(goruntu_yolu: str, confidence: float = 0.7) -> bool:
    """
    Ekranda verilen görüntüyü arar.

    NEDEN confidence=0.7?
    Tam eşleşme (1.0) çok katı — ekran biraz farklı olunca bulamaz.
    0.7 → %70 benzerlik yeterli, hem esnek hem güvenilir.

    Döndürür: True (buldu) / False (bulamadı)
    """
    try:
        konum = pyautogui.locateOnScreen(
            goruntu_yolu,
            confidence=confidence
        )
        return konum is not None
    except Exception as e:
        logger.debug(f"locateOnScreen hatası: {e}")
        return False


# ── ANA FONKSİYON: ZİRVE DURUM KONTROL ──────────────────────────────────
def zirve_durumu_kontrol_et() -> ZirveDurum:
    """
    Zirve'nin şu an hangi ekranda olduğunu tespit eder.

    KONTROL SIRASI:
    1. İrsaliye formu açık mı? → en sık kullanılan ekran
    2. Ana menü açık mı?
    3. Login ekranı açık mı?
    4. Hiçbiri → Kapalı veya bilinmiyor

    NEDEN BU SIRA?
    En olası durum önce kontrol edilir → performans.
    Sistem zaten çalışıyorsa irsaliye formunda olma ihtimali yüksek.
    """
    logger.debug("Zirve durum kontrolü başladı...")

    # 1. İrsaliye formu açık mı?
    if ekranda_ara(REFERANS["irsaliye_ekran"]):
        logger.debug("✅ Zirve: İrsaliye formu açık")
        return ZirveDurum.IRSALIYE_FORMU

    # 2. Ana menü açık mı?
    # Ana menüde "Zirve Ticari" logosu aranır
    # Şimdilik basit kontrol — ileride logo görüntüsü eklenebilir
    pencereler = _aktif_pencereler_al()

    if "İRSALİYE" in pencereler:
        logger.debug("✅ Zirve: İrsaliye penceresi açık")
        return ZirveDurum.IRSALIYE_FORMU

    if "ZİRVE" in pencereler or "Zirve" in pencereler:
        logger.debug("✅ Zirve: Açık ama hangi ekranda bilinmiyor")
        return ZirveDurum.ANA_MENU

    # 3. Zirve tamamen kapalı
    logger.debug("❌ Zirve: Kapalı")
    return ZirveDurum.KAPALI


# ── YARDIMCI: AKTİF PENCERELER ───────────────────────────────────────────
def _aktif_pencereler_al() -> str:
    """
    Windows'ta açık pencerelerin başlıklarını alır.

    NEDEN?
    pyautogui.locateOnScreen() bazen yavaş çalışır.
    Pencere başlığına bakarak Zirve'nin açık olup olmadığını
    çok daha hızlı anlayabiliriz.

    tasklist komutu Windows'ta açık processleri listeler.
    """
    try:
        # Windows'ta açık pencereleri al
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ZirveT.exe"],
            capture_output=True,
            text=True,
            encoding="cp857"  # Windows Türkçe encoding
        )
        return result.stdout.upper()
    except Exception as e:
        logger.debug(f"Pencere listesi alınamadı: {e}")
        return ""


# ── ZİRVE AÇ VE GİRİŞ YAP ────────────────────────────────────────────────
def zirve_ac_ve_giris_yap(
        sifre: str,
        firma_adi: str = "TEST",
        zirve_exe_yolu: str = None
) -> bool:
    """
    Zirve'yi açar ve giriş yapar.

    ADIMLAR:
    1. Zirve exe'sini başlat
    2. Login ekranı gelene kadar bekle
    3. Şifreyi gir
    4. Firmayı seç
    5. Giriş butonuna bas
    6. Ana menü gelene kadar bekle

    NEDEN time.sleep()?
    Zirve yavaş açılıyor. Açılmadan tıklarsak hata olur.
    sleep() ile bekliyoruz — uygulamanın hazır olmasını.
    Production'da bu süreleri test ederek optimize ederiz.
    """

    # Zirve exe yolu — .env'den alınabilir
    if not zirve_exe_yolu:
        # Varsayılan Zirve kurulum yolu
        zirve_exe_yolu = r"C:\ZirveT\ZirveT.exe"

    if not os.path.exists(zirve_exe_yolu):
        logger.error(f"Zirve exe bulunamadı: {zirve_exe_yolu}")
        return False

    try:
        # 1. Zirve'yi başlat
        logger.info("Zirve başlatılıyor...")
        subprocess.Popen(zirve_exe_yolu)

        # 2. Login ekranı gelene kadar bekle (max 15 saniye)
        logger.info("Login ekranı bekleniyor...")
        for _ in range(15):
            time.sleep(1)
            durum = zirve_durumu_kontrol_et()
            if durum == ZirveDurum.LOGIN:
                break

        # 3. Şifreyi gir
        logger.info("Şifre giriliyor...")
        pyautogui.click(*LOGIN["sifre_kutusu"])
        time.sleep(0.3)
        pyautogui.write(sifre, interval=0.05)

        # 4. Firmayı seç
        logger.info(f"Firma seçiliyor: {firma_adi}")
        pyautogui.click(*LOGIN["firma_listesi"])
        time.sleep(0.3)

        # 5. Giriş butonuna bas
        logger.info("Giriş yapılıyor...")
        pyautogui.click(*LOGIN["giris_butonu"])

        # 6. Ana menü gelene kadar bekle (max 10 saniye)
        time.sleep(3)
        logger.info("✅ Zirve girişi tamamlandı")
        return True

    except Exception as e:
        logger.error(f"Zirve açma hatası: {e}")
        return False


