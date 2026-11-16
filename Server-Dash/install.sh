#!/usr/bin/env bash
# 서버 대시보드 설치 스크립트 (어느 systemd 리눅스 서버에서나 동작)
# 사용법:  zip 푼 폴더에서  bash install.sh
# 이미 설치돼 있어도 다시 실행하면 새 버전으로 업데이트돼 (config.yaml / secrets.yaml 은 있으면 안 건드림)
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"                     # 설치 위치는 어디든 상관없음 (서비스 파일에 이 경로가 들어감)
PY="$(command -v python3 || command -v python || true)"
VER="$(cat VERSION 2>/dev/null || echo "?")"
echo "═══ Server-Dash v$VER 설치/업데이트 시작 ($DIR) ═══"

echo "⓪ 필요한 것 확인"
[ -n "$PY" ] || { echo "✗ python3 가 없어. 먼저 설치해 줘 (예: sudo apt install -y python3 python3-venv)"; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "✗ systemd(systemctl) 가 없는 서버야. 이 대시보드는 systemd 리눅스용이야."; exit 1; }
command -v curl >/dev/null 2>&1 || echo "   (참고) curl 이 없어서 마지막 상태 확인만 건너뛸 수 있어"
if ! "$PY" -c "import venv, ensurepip" 2>/dev/null; then
  echo "✗ python3-venv 가 없어. 먼저 설치해 줘:  sudo apt install -y python3-venv   (Arch/CachyOS 는 python 에 포함이라 이 메시지가 안 떠)"; exit 1
fi

echo "① 파이썬 가상환경 만들고 라이브러리 설치"
"$PY" -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt

echo "①-2 설정 파일 준비 (config.yaml 이 없을 때만 예시로 만듦)"
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "   → config.yaml 새로 만듦. 서비스 목록 등을 내 서버에 맞게 고쳐 줘: nano $DIR/config.yaml"
fi
echo "①-3 알림용 secrets.yaml 준비 (이미 있으면 안 건드림)"
if [ ! -f secrets.yaml ]; then
  cp secrets.example.yaml secrets.yaml
  echo "   → secrets.yaml 새로 만듦. 웹훅 주소를 채워 줘: nano $DIR/secrets.yaml"
fi
chmod 600 secrets.yaml           # 나만 읽을 수 있게

echo "② ss 명령 하나만 비밀번호 없이 sudo 허용 (열린 포트의 프로세스 이름 보기용)"
SS="$(command -v ss || true)"
if [ -n "$SS" ] && command -v sudo >/dev/null 2>&1; then
  RULE="$USER ALL=(root) NOPASSWD: $SS -tlnpH"
  echo "$RULE" | sudo tee /etc/sudoers.d/bc250-dash >/dev/null
  sudo chmod 440 /etc/sudoers.d/bc250-dash
  sudo visudo -cf /etc/sudoers.d/bc250-dash   # 문법 검사 (틀리면 여기서 멈춤)
else
  echo "   → ss 또는 sudo 가 없어서 건너뜀 (포트의 프로세스 이름이 일부 안 보일 수 있어)"
fi

echo "③ 사용자 서비스로 등록하고 시작"
mkdir -p ~/.config/systemd/user
# 서비스 파일의 경로(%h/bc250-dash)를 실제 설치 위치로 바꿔서 넣음
for f in bc250-dash.service bc250-mcp-watchdog.service bc250-mcp-watchdog.timer; do
  sed "s|%h/bc250-dash|$DIR|g" "$f" > ~/.config/systemd/user/"$f"
done
systemctl --user daemon-reload
systemctl --user enable --now bc250-dash.service
systemctl --user restart bc250-dash.service      # 이미 설치돼 있던 경우 새 버전으로 다시 시작
# 로그아웃해도 계속 돌게 (이미 켜져 있으면 그대로)
loginctl enable-linger "$USER" 2>/dev/null || echo "   (참고) 로그아웃 후에도 돌리려면: sudo loginctl enable-linger $USER"
# mypc 자동 복구 타이머는 config.yaml 에 watchdog·hermes 설정이 있을 때만 켬
if .venv/bin/python -c "import sys,yaml; c=yaml.safe_load(open('config.yaml',encoding='utf-8')) or {}; sys.exit(0 if c.get('watchdog') and (c.get('hermes') or {}).get('unit') else 1)"; then
  systemctl --user enable --now bc250-mcp-watchdog.timer
else
  systemctl --user disable --now bc250-mcp-watchdog.timer 2>/dev/null || true
  echo "   (watchdog 설정이 없어서 자동 복구 타이머는 안 켬)"
fi

echo "④ 상태 확인"
sleep 3
PORT="$(.venv/bin/python -c "import yaml; c=yaml.safe_load(open('config.yaml',encoding='utf-8')) or {}; print((c.get('dashboard') or {}).get('port',5070))")"
systemctl --user --no-pager status bc250-dash.service | head -5
curl -s -o /dev/null -w "API 응답 코드: %{http_code}\n" "http://127.0.0.1:$PORT/api/status"
echo "═══ Server-Dash v$VER 설치 완료 ═══  (브라우저에서 Ctrl+F5 한 뒤 맨 아래 푸터가 v$VER 인지 확인해 줘)"
echo "끝! 브라우저에서 http://127.0.0.1:$PORT 로 열어 봐 (밖에서 보려면 터널/리버스 프록시를 연결하고, 꼭 로그인 보호를 걸어 줘)"
