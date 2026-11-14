@echo off
rem Comparateur de films PLEX - double-cliquer, ou : lancer.bat "D:\Films"
title Comparateur de films PLEX
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "BIB=%~1"
if "%BIB%"=="" set /p "BIB=Dossier de la bibliotheque de films : "
if not exist "%BIB%\_choix_a_faire.json" python -I trouver_doublons.py "%BIB%"
echo.
echo Comparateur de films PLEX : http://127.0.0.1:8770  (fermer cette fenetre pour arreter)
python -I comparateur.py --cas "%BIB%\_choix_a_faire.json" --choix "%BIB%\_mes_choix.json"
pause
