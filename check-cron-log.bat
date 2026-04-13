@echo off
cd /d "%~dp0"
ssh -i ***REDACTED-KEY*** ubuntu@***REDACTED-IP*** "tail -20 ~/get-ASAP/logs/cron.log"
pause
