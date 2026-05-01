@echo off
setlocal
set BASE_DIR=%~dp0
set GRADLE_VERSION=8.13
set DIST_URL=https://services.gradle.org/distributions/gradle-%GRADLE_VERSION%-bin.zip
set CACHE_DIR=%USERPROFILE%\.gradle\wrapper\dists\gradle-%GRADLE_VERSION%-bin\local
set INSTALL_DIR=%CACHE_DIR%\gradle-%GRADLE_VERSION%
set ZIP_FILE=%CACHE_DIR%\gradle-%GRADLE_VERSION%-bin.zip

if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"

if not exist "%INSTALL_DIR%\bin\gradle.bat" (
  if not exist "%ZIP_FILE%" (
    powershell -Command "Invoke-WebRequest -Uri '%DIST_URL%' -OutFile '%ZIP_FILE%'"
  )
  powershell -Command "Expand-Archive -Force '%ZIP_FILE%' '%CACHE_DIR%'"
)

"%INSTALL_DIR%\bin\gradle.bat" -p "%BASE_DIR%" %*
