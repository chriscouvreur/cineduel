@echo off
rem CinéDuel - pour Plex et Jellyfin / for Plex & Jellyfin
rem Double-cliquer, ou / Double-click, or:  CineDuel.bat "D:\Films"
title CineDuel
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "BIB=%~1"
if "%BIB%"=="" set /p "BIB=Dossier de vos films / Your movies folder : "
if not exist "%BIB%\_choix_a_faire.json" python -I trouver_doublons.py "%BIB%"
echo.
echo  http://127.0.0.1:8770
echo  Fermer cette fenetre pour arreter / Close this window to stop
python -I cineduel.py --cas "%BIB%\_choix_a_faire.json" --choix "%BIB%\_mes_choix.json"
pause
