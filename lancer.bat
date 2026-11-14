@echo off
rem Comparateur de films PLEX / PLEX Movie Comparator
rem Double-cliquer, ou / Double-click, or:  lancer.bat "D:\Films"
title Comparateur de films PLEX / PLEX Movie Comparator
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "BIB=%~1"
if "%BIB%"=="" set /p "BIB=Dossier de vos films / Your movies folder : "
if not exist "%BIB%\_choix_a_faire.json" python -I trouver_doublons.py "%BIB%"
echo.
echo  http://127.0.0.1:8770
echo  Fermer cette fenetre pour arreter / Close this window to stop
python -I comparateur.py --cas "%BIB%\_choix_a_faire.json" --choix "%BIB%\_mes_choix.json"
pause
