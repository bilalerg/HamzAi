# parser.py — Fabrika Cevabı Analizi
#
# SORUMLULUK:
# 1. Gelen mailden [Ref: HZ-XXXX] kodunu bulur
# 2. Claude NLP ile "onay mı itiraz mı?" kararı verir
# 3. DB'deki ilgili waybill'i günceller
# 4. ONAY → fatura_kes() çağırır
# 5. İTİRAZ → muhasebeciye bildirim gönderir

import re
import anthropic
from app.core.config import ANTHROPIC_API_KEY
from app.core.database import Waybill, TicketStatus
from app.core.logger import logger
from app.mail.sender import muhasebeciye_uyari_gonder


# ── FONKSİYON 1: REF KODU BUL ───────────────────────────────────────────
def ref_kodu_bul(mail_konusu: str, mail_govdesi: str) -> str | None:
    """
    Mail konu ve gövdesinde [Ref: HZ-XXXX] kodunu arar.
    Hem konuda hem gövdede arar — fabrika konuyu değiştirebilir.
    """
    tam_metin = f"{mail_konusu} {mail_govdesi}"
    pattern = r'\[Ref:\s*(HZ-\d+)\]'
    eslesme = re.search(pattern, tam_metin, re.IGNORECASE)

    if eslesme:
        ref_kodu = eslesme.group(1)
        logger.info(f"✅ Ref kodu bulundu: {ref_kodu}")
        return ref_kodu

    logger.warning("⚠️ Mail içinde ref kodu bulunamadı")
    return None


# ── FONKSİYON 2: NLP KARAR VER ──────────────────────────────────────────
def nlp_karar_ver(mail_govdesi: str) -> str:
    """
    Claude NLP ile mailin onay mı itiraz mı olduğunu belirler.

    NEDEN CLAUDE?
    Fabrika çok farklı şekillerde onay verebilir:
    "Onaylıyorum" / "Tamam" / "OK" / "Evet" / "Kabul"
    Regex bunu yapamaz — Claude yapabilir.

    max_tokens=10: Sadece ONAY veya ITIRAZ bekliyoruz — 10 token yeterli.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Aşağıdaki e-posta içeriğini analiz et.
Bu mail bir irsaliye onay/teyit sürecine aittir.

Mail içeriği:
{mail_govdesi}

Soru: Bu mail bir ONAY mı yoksa İTİRAZ mı bildiriyor?

Sadece tek kelime yaz: ONAY veya ITIRAZ
Başka hiçbir şey yazma."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}]
    )

    karar = response.content[0].text.strip().upper()
    karar = karar.replace("İ", "I").replace("Ş", "S").replace("Ğ", "G")
    logger.info(f"🤖 NLP kararı: {karar}")

    if karar not in ["ONAY", "ITIRAZ"]:
        logger.warning(f"⚠️ Beklenmedik NLP cevabı: {karar} → BELIRSIZ olarak işaretlendi")
        return "BELIRSIZ"

    return karar


# ── FONKSİYON 3: FABRİKA CEVABINI İŞLE ─────────────────────────────────
def fabrika_cevabi_isle(
        mail_konusu: str,
        mail_govdesi: str,
        muhasebeci_mail: str,
        db
) -> dict:
    """
    Fabrika cevabını uçtan uca işler.

    AKIŞ:
    1. Ref kodunu bul
    2. DB'de ilgili waybill'i bul
    3. NLP kararını al
    4. ONAY → fatura_kes() çağır
    5. İTİRAZ → muhasebeciye bildir
    """

    # ── ADIM 1: Ref kodunu bul ───────────────────────────────────────────
    ref_kodu = ref_kodu_bul(mail_konusu, mail_govdesi)

    if not ref_kodu:
        logger.warning("Tanımsız mail: ref kodu yok")
        muhasebeciye_uyari_gonder(
            muhasebeci_mail=muhasebeci_mail,
            konu="Tanımsız Teyit Maili",
            mesaj=f"Ref kodu olmayan bir teyit maili geldi.\n\nKonu: {mail_konusu}\n\nİçerik:\n{mail_govdesi}"
        )
        return {"basarili": False, "hata": "ref_kodu_yok"}

    # ── ADIM 2: DB'de waybill bul ────────────────────────────────────────
    waybill = db.query(Waybill).filter(
        Waybill.ref_kodu == ref_kodu
    ).first()

    if not waybill:
        logger.error(f"DB'de waybill bulunamadı: {ref_kodu}")
        muhasebeciye_uyari_gonder(
            muhasebeci_mail=muhasebeci_mail,
            konu="Eşleşmeyen Ref Kodu",
            mesaj=f"{ref_kodu} kodlu bir teyit maili geldi ama DB'de eşleşen irsaliye bulunamadı."
        )
        return {"basarili": False, "hata": "waybill_bulunamadi"}

    # ── ADIM 3: NLP kararı al ────────────────────────────────────────────
    karar = nlp_karar_ver(mail_govdesi)

    waybill.teyit_mail_icerik = mail_govdesi
    waybill.nlp_karar = karar

    # ── ADIM 4: Karara göre işlem yap ────────────────────────────────────
    if karar == "ONAY":
        logger.info(f"✅ Fabrika onayladı: {ref_kodu}")
        waybill.status = TicketStatus.FATURA_KESILIYOR
        db.commit()

        # Fatura kes — lazy import döngüsel bağımlılığı önler
        from app.parasut.fatura import fatura_kes
        fatura_sonuc = fatura_kes(waybill.id)

        if fatura_sonuc["basarili"]:
            logger.info(f"✅ Fatura kesildi: {fatura_sonuc['fatura_no']}")
        else:
            logger.error(f"❌ Fatura kesilemedi: {fatura_sonuc.get('hata')}")

        return {
            "basarili": True,
            "ref_kodu": ref_kodu,
            "karar": "ONAY",
            "waybill_id": waybill.id,
            "fatura_sonuc": fatura_sonuc
        }

    elif karar == "ITIRAZ":
        logger.warning(f"⚠️ Fabrika itiraz etti: {ref_kodu}")
        waybill.status = TicketStatus.FABRIKA_ITIRAZI
        db.commit()

        muhasebeciye_uyari_gonder(
            muhasebeci_mail=muhasebeci_mail,
            konu=f"Fabrika İtirazı — {ref_kodu}",
            mesaj=f"Fabrika irsaliyeye itiraz etti.\n\nRef: {ref_kodu}\n\nFabrika mesajı:\n{mail_govdesi}"
        )

        return {
            "basarili": True,
            "ref_kodu": ref_kodu,
            "karar": "ITIRAZ",
            "waybill_id": waybill.id
        }

    else:
        logger.warning(f"⚠️ Belirsiz fabrika cevabı: {ref_kodu}")

        muhasebeciye_uyari_gonder(
            muhasebeci_mail=muhasebeci_mail,
            konu=f"Belirsiz Fabrika Cevabı — {ref_kodu}",
            mesaj=f"Fabrika cevabı anlaşılamadı.\n\nRef: {ref_kodu}\n\nFabrika mesajı:\n{mail_govdesi}"
        )

        return {
            "basarili": False,
            "ref_kodu": ref_kodu,
            "karar": "BELIRSIZ",
            "waybill_id": waybill.id
        }