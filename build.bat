@echo off
setlocal
cd /d "%~dp0"

echo Installiere Abhaengigkeiten ...
python -m pip install -r requirements.txt || goto :error
python -m pip install pyinstaller || goto :error

echo.
echo Baue EXE ...
python -m PyInstaller --noconfirm --clean --onefile --windowed --icon icon.ico --add-data "icon.ico;." --version-file version_info.txt --name GoogleDriveFolderDownloader gdrive_downloader.py || goto :error

echo.
echo Fertig: dist\GoogleDriveFolderDownloader.exe
pause
exit /b 0

:error
echo.
echo Build fehlgeschlagen.
pause
exit /b 1
