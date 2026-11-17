#!/usr/bin/env fish
# ==========================================================
#  Server-Dash 배포 (fish 버전)
#  쓰는 법:  fish deploy.fish -v 1.51 -c v1.51 config.example.yaml 에 trusted_proxy 예시 추가
#    -v  새 버전 번호 (zip 안의 VERSION 과 같은지 확인용)
#    -c  커밋 메시지 (뒤에 오는 말 전부. 따옴표 없이 써도 돼)
#  준비: Server-Dash.zip 을 다운로드 폴더에 받아 둘 것
# ==========================================================

function fail
    echo ""
    echo "실패했어. 위 메시지를 확인해 줘."
    exit 1
end

function usage
    echo ""
    echo "쓰는 법:  fish deploy.fish -v 1.51 -c v1.51 config.example.yaml 에 trusted_proxy 예시 추가"
    echo "  -v 새 버전 번호,  -c 커밋 메시지 (둘 다 필요)"
    exit 1
end

# ---- 인자 읽기 ----
set -l VER ""
set -l MSG ""

while set -q argv[1]
    switch $argv[1]
        case -v
            if set -q argv[2]
                set VER $argv[2]
                set -e argv[1]
                set -e argv[1]
            else
                set -e argv[1]
            end
        case -c
            set -e argv[1]
            set MSG (string join -- ' ' $argv)
            set -e argv
        case '*'
            set -e argv[1]
    end
end

if test -n "$VER"
    set VER (string trim -- $VER)
end
if test -n "$MSG"
    set MSG (string trim -- $MSG)
end

if test -z "$VER"; or test -z "$MSG"
    usage
end

# ---- 작업 폴더로 이동 (~/Documents/Server-Dash) ----
cd "$HOME/Documents/Server-Dash"
if test $status -ne 0
    echo "작업 폴더로 이동 실패: $HOME/Documents/Server-Dash"
    fail
end

# 예전에 잘못 풀린 안쪽 Server-Dash 폴더가 있으면 지움
if test -d Server-Dash
    rm -rf Server-Dash
end

# 다운로드 폴더의 zip 을 현재 폴더에 덮어 풀기
command -v unzip >/dev/null
if test $status -ne 0
    echo "unzip 이 없어. 먼저 설치해 줘."
    fail
end
unzip -o "$HOME/Downloads/Server-Dash.zip" -d .
if test $status -ne 0
    echo "zip 풀기 실패: $HOME/Downloads/Server-Dash.zip"
    fail
end

# ---- 버전 확인: zip 안의 VERSION 이 -v 값과 같아야 함 (옛 zip 이면 여기서 멈춤) ----
if not test -f VERSION
    echo "zip 안에 VERSION 파일이 없어."
    fail
end
read -l FILEVER < VERSION
if test -n "$FILEVER"
    set FILEVER (string trim -- $FILEVER)
end

echo ""
echo "[VERSION] 파일: $FILEVER  /  요청: $VER"
if test "$FILEVER" != "$VER"
    echo "버전이 달라. 다운로드 폴더의 zip 이 최신인지 확인해 줘."
    fail
end

# ---- 커밋하고 GitHub 로 올리기 ----
git add .
if test $status -ne 0
    fail
end
git commit -m "$MSG"
if test $status -ne 0
    fail
end
git push
if test $status -ne 0
    fail
end

echo ""
echo "완료! 이제 각 서버에서 git pull 하고 bash install.sh 실행해"
read -l -P "계속하려면 Enter 를 눌러... " _dummy
exit 0
