@echo off
setlocal EnableExtensions
rem ============================================================
rem  BidKing - STEP 1 of 2: refresh BASE data only.
rem
rem  Extract Item/RankMap/Skill/Language from a game client
rem  StreamingAssets folder (optional), then derive
rem  item_prices.csv / rank_map_export.csv / Skill_export.csv.
rem
rem  It STOPS right there ON PURPOSE: you should then hand-delete
rem  event / limited items from  data\item_prices.csv.
rem  Drop tables, the HTML priors and map quality are NOT touched
rem  by this script.
rem
rem  Then run the second script:  refresh_downstream.bat
rem
rem  USAGE:
rem     refresh_client_data.bat [StreamingAssetsDir] [nopause]
rem
rem    StreamingAssetsDir : optional. If omitted, auto-search:
rem                          .\StreamingAssets  then  .\data\StreamingAssets
rem                          If still not found, extraction is SKIPPED and
rem                          the existing data\ CSV files are used as-is
rem                          (handy when you already replaced them manually).
rem    nopause            : do not wait for a key press at the end
rem
rem  ALL inputs/outputs live in the repo's data\ directory.
rem  Chinese guide: see  data\更新数据白话版.md
rem ============================================================

set "ROOT=%~dp0"
cd /d "%ROOT%"
if errorlevel 1 exit /b 1

set "DAT=%ROOT%data"
set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"

echo.
echo ============================================================
echo   BidKing  STEP 1/2  BASE data: extract + item_prices + rank + skill
echo ============================================================

rem ---------- 1) locate StreamingAssets ----------
set "SA="
if not "%~1"=="" if exist "%~1" set "SA=%~1"
if not defined SA if exist "%ROOT%StreamingAssets\yoo\DefaultPackage\*.bundle" set "SA=%ROOT%StreamingAssets"
if not defined SA if exist "%ROOT%data\StreamingAssets\yoo\DefaultPackage\*.bundle" set "SA=%ROOT%data\StreamingAssets"
if not defined SA if exist "%ROOT%StreamingAssets\*.bundle" set "SA=%ROOT%StreamingAssets"
if defined SA goto :have_sa

echo [1/6 WARN] StreamingAssets not found - client extraction SKIPPED.
echo           Continuing with the existing data\ CSV files.
goto :stage2

:have_sa
echo [1/6] StreamingAssets : %SA%

rem ---------- 2) ensure UnityPy dependency ----------
set "DEPS=%ROOT%tools\_deps"
if exist "%DEPS%\UnityPy\__init__.py" goto :extract_ok
echo [2/6] UnityPy not found. Installing into %DEPS% ...
python -m pip install --target "%DEPS%" UnityPy
if not errorlevel 1 goto :extract_ok
echo [ERROR] UnityPy install failed. Please run manually:
echo   python -m pip install --target "%DEPS%" UnityPy
goto :eof

:extract_ok
echo [2/6] Extracting Item/RankMap/Skill/Language into %DAT% ...
python "%ROOT%tools\refresh_from_client.py" --streaming "%SA%" --out-dir "%DAT%"
if errorlevel 1 echo [WARN] Table extraction failed. See messages above.

rem ---------- 3) derive item_prices.csv from Item + Language ----------
:stage2
echo [3/6] Building item_prices.csv ...
if exist "%DAT%\Item.csv" goto :do_prices
echo [SKIP] Item.csv missing - cannot build item_prices.csv.
goto :stage3

:do_prices
python -m bidking.tools.item_table --item-csv "%DAT%\Item.csv" --lang-csv "%DAT%\Language.csv" --out "%DAT%\item_prices.csv"
if errorlevel 1 echo [WARN] item_prices.csv build failed. See data\版本更新手册.md.

rem ---------- 4) export rank_map_export.csv (rank only; drop tables untouched) ----------
:stage3
echo [4/6] Building rank_map_export.csv ...
if not exist "%DAT%\RankMap.csv" goto :no_rank
python -m bidking.tools.game_tables --data-dir "%DAT%" --drop-csv-in "%DAT%\__no_drop__.csv"
if errorlevel 1 echo [WARN] rank_map_export.csv build failed.
goto :stage4

:no_rank
echo [SKIP] RankMap.csv missing - cannot build rank_map_export.csv.

rem ---------- 5) derive Skill_export.csv ----------
:stage4
echo [5/6] Building Skill_export.csv ...
if exist "%DAT%\Skill.csv" goto :do_skill
echo [SKIP] Skill.csv missing.
goto :summary

:do_skill
python -m bidking.tools.skill_table --skill-csv "%DAT%\Skill.csv" --out "%DAT%\Skill_export.csv"
if errorlevel 1 echo [WARN] Skill_export.csv build failed.

rem ---------- 6) pause point + hand-off message ----------
:summary
echo.
echo ============================================================
echo   STEP 1/2 done.
echo     extracted : Item.csv RankMap.csv Skill.csv Language.csv
echo                 only when a client StreamingAssets was found
echo     derived   : item_prices.csv  rank_map_export.csv  Skill_export.csv
echo.
echo   NEXT, MANUALLY:
echo     1. open   %DAT%\item_prices.csv
echo     2. delete the event / limited item rows you want gone
echo     3. save the file
echo.
echo   THEN run:    refresh_downstream.bat
echo   (it refreshes drop tables, HTML priors and map quality
echo    based on the item list you just trimmed)
echo ============================================================
echo.
if /i "%~2"=="nopause" goto :eof
if /i "%~3"=="nopause" goto :eof
pause
exit /b 0
