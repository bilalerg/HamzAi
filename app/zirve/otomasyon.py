import pyautogui
import time
from pywinauto import Application
from app.core.logger import logger
from app.core.database import SessionLocal, WeighTicket, Waybill, TicketStatus
from app.zirve.koordinatlar import LOGIN, ANA_MENU, IRSALIYE, POPUP
from app.zirve.sanity_check import ZirveDurum, zirve_durumu_kontrol_et, zirve_ac_ve_giris_yap

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.3


def tikla(x: int, y: int, bekle: float = 0.5):
    # Güvenli tıklama
    pyautogui.click(x, y)
    time.sleep(bekle)


def yaz(metin: str, temizle: bool = True):
    # Türkçe destekli yazma — pyperclip ile kopyala yapıştır
    if temizle:
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.1)
    import pyperclip
    pyperclip.copy(metin)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)


def popup_kontrol_et() -> bool:
    # Popup varsa Tamam'a basar
    try:
        app = Application(backend="uia").connect(title_re=".*TİCARİ.*")
        for pencere in app.windows():
            baslik = pencere.window_text()
            if "TİCARİ (T)" in baslik or "Uyarı" in baslik:
                logger.warning(f"Popup tespit edildi: {baslik}")
                try:
                    pencere.child_window(title="Tamam").click()
                    time.sleep(0.5)
                    return True
                except:
                    tikla(*POPUP["tamam"])
                    return True
        return False
    except Exception as e:
        logger.debug(f"Popup kontrol hatası: {e}")
        return False


def irsaliye_no_oku() -> str:
    # Kaydetmeden ÖNCE çağrılmalı — form açıkken irsaliye no alanını okur
    try:
        import pyperclip
        pyautogui.click(200, 419)
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.2)
        deger = pyperclip.paste().strip()
        if deger and deger.isdigit():
            logger.info(f"İrsaliye No okundu: {deger}")
            return deger
        return None
    except Exception as e:
        logger.warning(f"İrsaliye no okunamadı: {e}")
        return None


def irsaliye_olustur(
        ticket_id: int,
        cari_adi: str,
        stok_kodu: str,
        miktar: int,
        tarih: str,
        sifre: str = "1234"
) -> dict:
    db = SessionLocal()
    ticket = None

    try:
        ticket = db.query(WeighTicket).filter(WeighTicket.id == ticket_id).first()

        if ticket:
            ticket.status = TicketStatus.ZIRVE_DEVAM_EDIYOR
            db.commit()
            logger.info(f"Status güncellendi: ZIRVE_DEVAM_EDIYOR (ticket_id={ticket_id})")

        # Mevcut waybill varsa tekrar oluşturma
        ref_kodu = f"HZ-{ticket_id:04d}"
        mevcut = db.query(Waybill).filter(Waybill.ref_kodu == ref_kodu).first()
        if mevcut:
            logger.info(f"Waybill zaten var: {ref_kodu}")
            return {
                "basarili": True,
                "irsaliye_no": mevcut.irsaliye_no,
                "ref_kodu": ref_kodu,
                "waybill_id": mevcut.id,
                "hata": None
            }

        # Sanity check
        logger.info("Zirve durum kontrolü...")
        durum = zirve_durumu_kontrol_et()

        if durum == ZirveDurum.KAPALI:
            logger.info("Zirve kapalı, açılıyor...")
            if not zirve_ac_ve_giris_yap(sifre=sifre):
                raise Exception("Zirve açılamadı")
            time.sleep(2)
        elif durum == ZirveDurum.LOGIN:
            zirve_ac_ve_giris_yap(sifre=sifre)
            time.sleep(2)

        # Ana menüden irsaliyeye git
        logger.info("Ana menüden irsaliyeye gidiliyor...")
        tikla(*ANA_MENU["irsaliye_butonu"])
        time.sleep(1)
        pyautogui.hotkey("ctrl", "F3")
        time.sleep(0.5)
        popup_kontrol_et()

        # Cari gir
        logger.info(f"Cari giriliyor: {cari_adi}")
        tikla(*IRSALIYE["cari_unvani"])
        yaz(cari_adi)
        pyautogui.press("enter")
        time.sleep(0.5)
        popup_kontrol_et()

        # Stok gir
        logger.info(f"Stok kodu giriliyor: {stok_kodu}")
        tikla(*IRSALIYE["stok_kodu"])
        yaz(stok_kodu)
        pyautogui.press("enter")
        time.sleep(0.5)
        popup_kontrol_et()

        # Miktar gir
        logger.info(f"Miktar giriliyor: {miktar}")
        tikla(*IRSALIYE["miktar"])
        yaz(str(miktar))
        pyautogui.press("tab")
        time.sleep(0.3)

        # İrsaliye no'yu kaydetmeden ÖNCE oku — form kapanınca alan temizleniyor
        irsaliye_no = irsaliye_no_oku()

        # Kaydet
        logger.info("İrsaliye kaydediliyor...")
        tikla(*IRSALIYE["kaydet"])
        time.sleep(1)
        popup_kontrol_et()

        # Status güncelle
        if ticket:
            ticket.status = TicketStatus.ZIRVE_TAMAMLANDI
            db.commit()

        # Waybill kaydet
        waybill = Waybill(
            irsaliye_no=irsaliye_no,
            ref_kodu=ref_kodu,
            ticket_id=ticket_id,
            status=TicketStatus.FABRIKA_BEKLENIYOR
        )
        db.add(waybill)
        db.commit()
        db.refresh(waybill)

        logger.info(f"✅ İrsaliye oluşturuldu: {irsaliye_no} [Ref: {ref_kodu}]")
        return {
            "basarili": True,
            "irsaliye_no": irsaliye_no,
            "ref_kodu": ref_kodu,
            "waybill_id": waybill.id,
            "hata": None
        }

    except Exception as e:
        logger.error(f"İrsaliye oluşturma hatası: {e}")
        if ticket:
            ticket.status = TicketStatus.HATA
            ticket.hata_mesaji = str(e)
            try:
                db.commit()
            except:
                db.rollback()
        return {"basarili": False, "irsaliye_no": None, "hata": str(e)}

    finally:
        db.close()


def irsaliye_olustur_retry(
        ticket_id: int,
        cari_adi: str,
        stok_kodu: str,
        miktar: int,
        tarih: str,
        max_deneme: int = 3,
        bekleme_suresi: int = 30
) -> dict:
    # 3 deneme hakkı ile irsaliye oluşturur
    from app.core.config import ZIRVE_PASSWORD
    sonuc = {}
    for deneme in range(1, max_deneme + 1):
        logger.info(f"İrsaliye denemesi {deneme}/{max_deneme}")
        sonuc = irsaliye_olustur(
            ticket_id=ticket_id,
            cari_adi=cari_adi,
            stok_kodu=stok_kodu,
            miktar=miktar,
            tarih=tarih,
            sifre=ZIRVE_PASSWORD
        )
        if sonuc["basarili"]:
            return sonuc
        if deneme < max_deneme:
            logger.warning(f"Deneme {deneme} başarısız, {bekleme_suresi}sn bekleniyor...")
            time.sleep(bekleme_suresi)
    logger.error(f"İrsaliye {max_deneme} denemede de oluşturulamadı!")
    return sonuc