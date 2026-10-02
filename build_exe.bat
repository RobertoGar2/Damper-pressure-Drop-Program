@echo off
REM Build a standalone Windows program with PyInstaller.
REM Output: dist\DamperDP.exe with a dist\data folder next to it.

pip install -r requirements-dev.txt || goto :error
pyinstaller --noconfirm --onefile --windowed --name DamperDP main.py || goto :error
xcopy /E /I /Y data dist\data >nul || goto :error

echo.
echo Done. Run dist\DamperDP.exe
echo Keep the data folder next to the .exe - edit the CSV there to add test data.
goto :eof

:error
echo.
echo Build failed.
exit /b 1
