@echo off
REM Wrapper for Windows Task Scheduler: cd into the project, run one check,
REM and append console output to bot.log so you can see what happened.
cd /d D:\trading
"C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe" bot.py run >> bot.log 2>&1
