@echo off
setlocal EnableExtensions
rem ============================================================
rem  BidKing - STEP 2 of 2: downstream refresh.
rem
rem  Run this AFTER you ran refresh_client_data.bat (STEP 1) and
rem  hand-deleted event / limited items from  data\item_prices.csv.
rem
rem  Pipeline:
rem     A. official drop rows   Drop.csv -> drop_table_weights.csv
rem                             and the DROP section of
rem                             calculator_data_merged.csv
rem                             (skipped when Drop.csv is absent:
rem                              existing drop tables are kept, only
rem                              prices downstream get refreshed)
rem     B. approx-drop pass     items in item_prices.csv that the
rem                             official graph does not mention are
rem                             given approx edges -> both files above
rem     C. HTML priors          recompute NEST_W / SUBMAP_PRIOR_MULT
rem                             in 物品轮廓爆率推断器.html and sync
rem                             EMBED_CSV.item from item_prices.csv
rem     D. map quality          map_quality_avg / p25 / p50
rem
rem  USAGE:
rem     refresh_downstream.bat [nopause]
rem
rem    nopause : do not wait for a key press at the end
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
echo   BidKing  STEP 2/2  drop + html + map quality refresh
echo ============================================================

rem ---------- 0) gate: downstream needs item_prices + merged csv ----------
if exist "%DAT%\item_prices.csv" goto :gate_prices_ok
echo [ERROR] %DAT%\item_prices.csv not found.
echo         Run refresh_client_data.bat first, then edit, then rerun this.
goto :end_fail

:gate_prices_ok
if exist "%DAT%\calculator_data_merged.csv" goto :gate_merged_ok
echo [ERROR] %DAT%\calculator_data_merged.csv not found.
echo         Its ITEM rows are preserved from the repo history.
echo         If it is missing you must restore it manually first.
goto :end_fail

:gate_merged_ok
set "HAS_DROP=0"
set "HAS_HTML=0"
if exist "%DAT%\Drop.csv" set "HAS_DROP=1"
if exist "%DAT%\物品轮廓爆率推断器.html" set "HAS_HTML=1"

rem ---------- A) official drop rows (only when Drop.csv is present) ----------
if "%HAS_DROP%"=="1" goto :do_official
echo [A] Drop.csv absent - official drop merge skipped.
echo     Existing drop tables kept; new item prices still flow downstream.
goto :B

:do_official
echo [A] Merging official drop rows into drop_table_weights.csv and calculator_data_merged.csv ...
python -m bidking.tools.game_tables --data-dir "%DAT%" --merge-calculator "%DAT%\calculator_data_merged.csv" --merge-out "%DAT%\calculator_data_merged.csv"
if errorlevel 1 echo [WARN] official drop merge failed.

rem ---------- B) approx-drop pass for items the official graph lacks ----------
:B
if "%HAS_DROP%"=="1" goto :do_approx
echo [B] Drop.csv absent - approx pass skipped.
goto :C

:do_approx
echo [B] Approx-drop pass on the trimmed item list ...
python "%ROOT%tools\add_approx_drop.py"
if errorlevel 1 echo [WARN] add_approx_drop.py failed.

rem ---------- C) recompute HTML priors + sync EMBED_CSV.item ----------
:C
if "%HAS_HTML%"=="1" goto :do_html
echo [C] 物品轮廓爆率推断器.html missing - HTML priors skipped.
goto :D

:do_html
echo [C] Recomputing HTML NEST_W / SUBMAP_PRIOR_MULT and syncing EMBED_CSV.item ...
python "%ROOT%tools\update_infer_html_embed.py" --sync-embed-item
if errorlevel 1 echo [WARN] update_infer_html_embed.py failed.

rem ---------- D) map quality avg / p25 / p50 ----------
:D
echo [D] Recomputing map_quality_avg / p25 / p50 ...
python "%ROOT%tools\map_quality_avg_prices.py"
if errorlevel 1 echo [WARN] map_quality_avg_prices.py failed.
python "%ROOT%tools\map_quality_p25_p50_prices.py"
if errorlevel 1 echo [WARN] map_quality_p25_p50_prices.py failed.

rem ---------- summary ----------
echo.
echo ============================================================
echo   STEP 2/2 done.
echo     drop      : drop_table_weights.csv  calculator_data_merged.csv
echo     html      : 物品轮廓爆率推断器.html  NEST_W / SUBMAP_PRIOR_MULT,
echo                 EMBED_CSV.item synced from the trimmed item_prices.csv
echo     map       : map_quality_avg_out.csv  map_quality_p25_out.csv
echo                 map_quality_p50_out.csv
echo.
echo   CHECK: if an item you deleted still shows up in
echo     %DAT%\drop_table_weights.csv
echo   its rows come from the official Drop.csv (not from the approx
echo   pass). To remove it completely, Drop.csv must be cleaned too.
echo   You can hand me the id list - I will strip it in one go.
echo ============================================================
echo.
if /i "%~1"=="nopause" goto :eof
if /i "%~2"=="nopause" goto :eof
pause
exit /b 0

:end_fail
echo.
if /i "%~1"=="nopause" goto :eof
if /i "%~2"=="nopause" goto :eof
pause
exit /b 1
