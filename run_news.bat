@echo off
REM Wrapper for Windows Task Scheduler: run the 12-hour macro/news report and
REM append console output to news.log.
cd /d D:\trading
"C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe" bot.py news >> news.log 2>&1
