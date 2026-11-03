# app/routers/muhasebe.py — Muhasebe sağlayıcı durumu + Excel'e aktarma

import asyncio
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.auth import mevcut_kullanici
from app.core.config import ANTHROPIC_API_KEY
from app.core.logger import logger
from app.routers.deps import _tenant_id_from_user, _guvenli_hata

router = APIRouter()


@router.get("/api/muhasebe/durum")
async def muhasebe_durum(request: Request):
    """Arayüzün hangi sistemle çalıştığını göstermesi için."""
    from app.muhasebe import aktif_saglayici, saglayici_adi
    return JSONResponse({
        "saglayici":   aktif_saglayici(),           # 'yerel' | 'parasut'
        "ad":          saglayici_adi(),
        "excel_aktar": aktif_saglayici() == "yerel",
        "ai_aktif":    bool(ANTHROPIC_API_KEY),     # False → sohbet anahtar-kelime, OCR elle giriş
    })


@router.get("/api/muhasebe/excel")
async def muhasebe_excel(request: Request):
    """Yerel muhasebe kayıtlarının tamamını .xlsx olarak indirir."""
    from app.muhasebe import aktif_saglayici
    if aktif_saglayici() != "yerel":
        return JSONResponse({"hata": "Excel'e aktarma yerel muhasebe modunda kullanılabilir."}, status_code=400)
    tenant_id = _tenant_id_from_user(mevcut_kullanici(request))
    try:
        from app.muhasebe.yerel.excel import excel_olustur
        icerik = await asyncio.to_thread(excel_olustur, tenant_id)
        dosya = f"profaix_muhasebe_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return Response(
            content=icerik,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{dosya}"'},
        )
    except Exception as e:
        logger.error(f"Excel aktarma hatası: {e}")
        return JSONResponse({"hata": _guvenli_hata(e)}, status_code=500)
