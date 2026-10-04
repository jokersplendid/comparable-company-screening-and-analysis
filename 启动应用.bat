@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -Command "try { $r=Invoke-WebRequest 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 2; if ($r.StatusCode -eq 200 -and $r.Content -eq 'ok') { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 (
  echo The app is already running at http://localhost:8501
  start "" "http://localhost:8501"
  pause
  exit /b
)
if exist ".venv\Scripts\streamlit.exe" (
  ".venv\Scripts\streamlit.exe" run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
) else (
  py -3.11 -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
)
pause
