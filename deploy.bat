@echo off
chcp 65001 >nul
REM ==========================================================
REM  Server-Dash 배포 (v1.51)
REM  쓰는 법: Server-Dash.zip 을 다운로드 폴더에 받은 뒤 이 파일을 더블클릭
REM ==========================================================

REM 작업 폴더로 이동 (내 문서\Server-Dash)
cd /d "%USERPROFILE%\Documents\Server-Dash" || goto :fail

REM 예전에 잘못 풀린 안쪽 Server-Dash 폴더가 있으면 지움
if exist "Server-Dash" rmdir /s /q "Server-Dash"

REM 다운로드 폴더의 zip 을 현재 폴더에 덮어 풀기
powershell -NoProfile -Command "Expand-Archive -Path \"$HOME\Downloads\Server-Dash.zip\" -DestinationPath . -Force" || goto :fail

REM 버전 확인 (여기가 1.51 로 나와야 정상)
echo.
echo [VERSION]
type VERSION
echo.

REM 변경 파일 담기
git add . || goto :fail

REM 커밋
git commit -m "v1.51 config.example.yaml 에 trusted_proxy 예시 추가" || goto :fail

REM GitHub 로 올리기
git push || goto :fail

echo.
echo 완료! 이제 각 서버에서 git pull 하고 bash install.sh 실행해
pause
exit /b 0

:fail
echo.
echo 실패했어. 위 메시지를 확인해 줘.
pause
