@echo off
chcp 65001 >nul
rem Copiar_Otros_FPS.bat  -  arrastra la carpeta del proyecto sobre este archivo, o abrilo con doble clic
where python >nul 2>&1 || (echo No encuentro Python. Instalalo desde python.org tildando "Add python.exe to PATH". & pause & exit /b)
set PYTHONIOENCODING=utf-8
python "%~dp0copiar_otros_fps.py" %*
echo.
pause
