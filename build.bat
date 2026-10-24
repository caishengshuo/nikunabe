@echo off
rem Build single-file exe with PyInstaller -> dist\GalgameJP.exe
rem Requires: python -m pip install pyinstaller pillow
rem
rem NOTE: keep this file ASCII-only. cmd.exe parses .bat with the OEM codepage
rem (936/GBK on Chinese Windows); UTF-8 Chinese literals here break the parser
rem ("'...' is not recognized as an internal or external command").
rem The Chinese exe name is applied by pack_release.py, which is UTF-8 safe.
setlocal
cd /d "%~dp0"

rem assets\app.ico is committed; only regenerate when it is missing, otherwise
rem other machines would overwrite the repo icon with the fallback drawing.
if exist "assets\app.ico" (
  echo [1/2] icon found, skipping generation ^(regenerate: python make_icon.py^)
) else (
  echo [1/2] assets\app.ico missing, generating...
  python make_icon.py || goto :err
)

echo [2/2] running PyInstaller...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name GalgameJP ^
  --icon assets\app.ico ^
  --add-data "galtl\ps;galtl\ps" ^
  --add-data "assets;assets" ^
  --hidden-import tkinter ^
  --hidden-import tkinter.font ^
  main.py || goto :err

echo.
echo done: dist\GalgameJP.exe
exit /b 0

:err
echo.
echo build failed, see the errors above.
exit /b 1
