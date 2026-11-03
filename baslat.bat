@echo off
REM Profaix - Windows icin tek tikla lokal baslatma
REM Ilk calistirmada sanal ortam kurar ve paketleri yukler (internet gerekir).
REM Demo veriyi sifirlamak icin:  baslat.bat --sifirla
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Sanal ortam olusturuluyor...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 ( echo Python bulunamadi. https://www.python.org/downloads/ adresinden Python 3.12 kurun. & pause & exit /b 1 )
  echo [2/2] Paketler yukleniyor, birkac dakika surebilir...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 ( echo Paket kurulumu basarisiz. & pause & exit /b 1 )
)
".venv\Scripts\python.exe" yerel_baslat.py %*
pause
