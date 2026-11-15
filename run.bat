@echo off
cd /d %~dp0
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
:loop
python bot.py >> data\bot.log 2>&1
echo bot exited, restart in 10s >> data\bot.log
timeout /t 10 >nul
goto loop
