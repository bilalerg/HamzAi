# app/parasut/irsaliye.py — GERİYE UYUMLULUK SHIM'i
# Fiş → irsaliye akışı sağlayıcıdan bağımsız hâle getirildi ve
# app/muhasebe/irsaliye_akisi.py'ye taşındı.
from app.muhasebe.irsaliye_akisi import (  # noqa: F401
    irsaliye_olustur, toplu_irsaliye_olustur, irsaliye_onayla, _tarih_formatla,
)
