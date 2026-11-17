@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
REM ==========================================================
REM  Server-Dash 배포
REM  쓰는 법:  deploy.bat -v 1.51 -c v1.51 config.example.yaml 에 trusted_proxy 예시 추가
REM    -v  새 버전 번호 (zip 안의 VERSION 과 같은지 확인용)
REM    -c  커밋 메시지 (뒤에 오는 말 전부. 따옴표 없이 써도 돼)
REM  준비: Server-Dash.zip 을 다운로드 폴더에 받아 둘 것
REM ==========================================================

REM ---- 인자 읽기 ----
set "VER="
set "MSG="
:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="-v" (set "VER=%~2" & shift & shift & goto :parse)
if /i "%~1"=="-c" (shift & goto :collect)
shift
goto :parse

REM -c 뒤에 오는 단어를 전부 이어 붙여 커밋 메시지로 씀
:collect
if "%~1"=="" goto :parsed
if defined MSG (set "MSG=!MSG! %~1") else (set "MSG=%~1")
shift
goto :collect

:parsed
if not defined VER goto :usage
if not defined MSG goto :usage

REM ---- 작업 폴더로 이동 (내 문서\Server-Dash) ----
cd /d "%USERPROFILE%\Documents\Server-Dash" || goto :fail

REM 예전에 잘못 풀린 안쪽 Server-Dash 폴더가 있으면 지움
if exist "Server-Dash" rmdir /s /q "Server-Dash"

REM 다운로드 폴더의 zip 을 현재 폴더에 덮어 풀기
powershell -NoProfile -Command "Expand-Archive -Path ($HOME + '\Downloads\Server-Dash.zip') -DestinationPath . -Force" || goto :fail

REM ---- 버전 확인: zip 안의 VERSION 이 -v 값과 같아야 함 (옛 zip 이면 여기서 멈춤) ----
set /p FILEVER=<VERSION
echo.
echo [VERSION] 파일: !FILEVER!  /  요청: !VER!
if not "!FILEVER!"=="!VER!" (
  echo 버전이 달라. 다운로드 폴더의 zip 이 최신인지 확인해 줘.
  goto :fail
)

REM ---- 커밋하고 GitHub 로 올리기 ----
git add . || goto :fail
git commit -m "!MSG!" || goto :fail
git push || goto :fail

echo.
echo 완료! 이제 각 서버에서 git pull 하고 bash install.sh 실행해
pause
exit /b 0

:usage
echo.
echo 쓰는 법:  deploy.bat -v 1.51 -c v1.51 config.example.yaml 에 trusted_proxy 예시 추가
echo   -v 새 버전 번호,  -c 커밋 메시지 (둘 다 필요)
pause
exit /b 1

:fail
echo.
echo 실패했어. 위 메시지를 확인해 줘.
pause
exit /b 1