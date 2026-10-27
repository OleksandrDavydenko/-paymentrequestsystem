@echo off
chcp 65001 >nul
rem Збирає PayDeskPayments.epf з вихідних файлів src\ (потрібна 1С:Підприємство 8.3 з ліцензією).
rem Інформаційна база потрібна лише, щоб запустити конфігуратор, — вона не змінюється.
rem Запуск: build.bat [шлях до файлової бази]   (за замовчуванням C:\Users\od\Documents\InfoBase1)
setlocal
set "IB=%~1"
if "%IB%"=="" set "IB=C:\Users\od\Documents\InfoBase1"
set "V8="
for /d %%v in ("%ProgramFiles%\1cv8\8.3.*") do if exist "%%v\bin\1cv8.exe" set "V8=%%v\bin\1cv8.exe"
if "%V8%"=="" for /d %%v in ("%ProgramFiles(x86)%\1cv8\8.3.*") do if exist "%%v\bin\1cv8.exe" set "V8=%%v\bin\1cv8.exe"
if "%V8%"=="" (
  echo Не знайдено 1cv8.exe у "%ProgramFiles%\1cv8"
  exit /b 1
)
if exist "%~dp0PayDeskPayments.epf" del "%~dp0PayDeskPayments.epf"
echo Платформа: %V8%
echo База: %IB%
"%V8%" DESIGNER /F"%IB%" /LoadExternalDataProcessorOrReportFromFiles "%~dp0src\PayDeskPayments.xml" "%~dp0PayDeskPayments.epf" /Out "%~dp0build.log" /DisableStartupDialogs /DisableStartupMessages
type "%~dp0build.log"
if exist "%~dp0PayDeskPayments.epf" (
  echo Готово: %~dp0PayDeskPayments.epf
) else (
  echo Не зібрано — текст помилки вище, у build.log
  exit /b 1
)
