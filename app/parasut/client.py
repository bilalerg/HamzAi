# client.py — Paraşüt API İstemcisi
#
# SORUMLULUK:
# 1. OAuth2 token alır
# 2. Token'ı bellekte saklar
# 3. Süresi dolunca refresh_token ile yeniler
# 4. Diğer modüllere hazır token verir
#
# NEDEN TOKEN YÖNETİMİ AYRI DOSYADA?
# Her API isteğinden önce token gerekiyor.
# Bunu her modülde tekrar yazmak yerine tek yerden yönetiyoruz.
# irsaliye.py ve fatura.py sadece get_token() çağırır, detayları bilmez.

import requests
import time
from app.core.config import (
    PARASUT_CLIENT_ID,
    PARASUT_CLIENT_SECRET,
    PARASUT_USERNAME,
    PARASUT_PASSWORD,
    PARASUT_COMPANY_ID,
    PARASUT_BASE_URL
)
from app.core.logger import logger

# ── TOKEN BELLEKTE SAKLANIR ───────────────────────────────────────────
# Bu değişkenler modül yüklenince oluşur, program çalıştığı sürece bellekte kalır.
# Her istek için yeni token almak yerine bunu kullanıyoruz.
#
# NEDEN GLOBAL DEĞİŞKEN?
# Token alma işlemi HTTP isteği — her seferinde yapmak hem yavaş hem gereksiz.
# Token 2 saat geçerli, biz süresi dolunca yeniliyoruz.
_access_token = None
_refresh_token = None
_token_expires_at = 0  # Unix timestamp — token ne zaman geçersiz olacak


# ── FONKSİYON 1: YENİ TOKEN AL ───────────────────────────────────────
def _token_al() -> dict:
    """
    Paraşüt'ten yeni access token alır.

    NEDEN PASSWORD GRANT?
    OAuth2'nin birkaç akışı var. Biz 'password' grant kullanıyoruz.
    Bu akışta kullanıcı adı + şifre ile direkt token alınıyor.
    Tarayıcı yönlendirmesi gerektirmiyor — arka plan servisleri için ideal.
    """
    logger.info("Paraşüt'ten yeni token alınıyor...")

    response = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type": "password",
            "client_id": PARASUT_CLIENT_ID,
            "client_secret": PARASUT_CLIENT_SECRET,
            "username": PARASUT_USERNAME,
            "password": PARASUT_PASSWORD,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"
        }
    )

    if response.status_code != 200:
        logger.error(f"Token alınamadı: {response.status_code} — {response.text}")
        raise Exception(f"Paraşüt token hatası: {response.status_code}")

    veri = response.json()
    logger.info("✅ Paraşüt token alındı")
    return veri


# ── FONKSİYON 2: TOKEN YENİLE ─────────────────────────────────────────
def _token_yenile(refresh_token: str) -> dict:
    """
    Mevcut refresh_token ile yeni access_token alır.

    NEDEN REFRESH TOKEN?
    Access token 2 saatte bir geçersiz oluyor.
    Refresh token ile yeni access token almak,
    kullanıcı adı + şifre tekrar göndermekten daha güvenli.
    Refresh token çok daha uzun süre geçerli.
    """
    logger.info("Paraşüt token yenileniyor...")

    response = requests.post(
        "https://api.parasut.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": PARASUT_CLIENT_ID,
            "client_secret": PARASUT_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"
        }
    )

    if response.status_code != 200:
        logger.warning(f"Token yenileme başarısız, yeni token alınıyor: {response.status_code}")
        return _token_al()

    veri = response.json()
    logger.info("✅ Paraşüt token yenilendi")
    return veri


# ── FONKSİYON 3: GEÇERLİ TOKEN VER ──────────────────────────────────
def get_token() -> str:
    """
    Geçerli access token döndürür.
    Token yoksa alır, süresi dolduysa yeniler.

    Bu fonksiyon dışarıdan çağrılacak tek nokta.
    irsaliye.py ve fatura.py sadece bunu çağırır.

    NEDEN time.time() - 60?
    Token tam süresi dolunca değil, 60 saniye öncesinden yeniliyoruz.
    Böylece token'ın tam dolma anında istek atmış olmayız.
    "Güvenlik payı" bırakıyoruz.
    """
    global _access_token, _refresh_token, _token_expires_at

    # Token hiç alınmamış
    if _access_token is None:
        veri = _token_al()

    # Token süresi dolmak üzere (60 saniye kala yenile)
    elif time.time() > _token_expires_at - 60:
        veri = _token_yenile(_refresh_token)

    # Token hâlâ geçerli — direkt döndür
    else:
        return _access_token

    # Yeni token bilgilerini sakla
    _access_token = veri["access_token"]
    _refresh_token = veri["refresh_token"]
    # expires_in saniye cinsinden — şu anki zamana ekle
    _token_expires_at = time.time() + veri["expires_in"]

    return _access_token


# ── FONKSİYON 4: API İSTEĞİ GÖNDER ──────────────────────────────────
def parasut_get(endpoint: str) -> dict:
    """
    Paraşüt API'ye GET isteği atar.
    endpoint örnek: "/827397/contacts"
    """
    token = get_token()
    url = f"{PARASUT_BASE_URL}{endpoint}"

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )

    if response.status_code != 200:
        logger.error(f"Paraşüt GET hatası: {response.status_code} — {response.text}")
        raise Exception(f"Paraşüt API hatası: {response.status_code}")

    return response.json()


def parasut_post(endpoint: str, veri: dict) -> dict:
    """
    Paraşüt API'ye POST isteği atar.
    endpoint örnek: "/827397/shipment_documents"
    veri: gönderilecek JSON body
    """
    token = get_token()
    url = f"{PARASUT_BASE_URL}{endpoint}"

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/vnd.api+json"
        },
        json=veri
    )

    # 201 Created veya 200 OK — ikisi de başarılı
    if response.status_code not in [200, 201]:
        logger.error(f"Paraşüt POST hatası: {response.status_code} — {response.text}")
        raise Exception(f"Paraşüt API hatası: {response.status_code} — {response.text}")

    return response.json()