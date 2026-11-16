# Server-Dash

리눅스(systemd) 서버의 상태를 한 화면에서 보는 가벼운 웹 대시보드야.
CPU·메모리·디스크·온도, 서비스 상태, 열린 포트를 보여주고, 문제가 생기면 Mattermost · Slack · Discord · Telegram · ntfy(폰 푸시) · 직접 만든 웹훅 중 원하는 곳으로 알려줘.
서비스 줄의 **[관리]** 버튼으로 `start / stop / restart / enable / disable` 도 할 수 있어.

- Python + Flask 한 개짜리 앱 (DB 없음)
- 폰·PC 둘 다 쓰기 편한 화면, 홈 화면에 앱으로 설치 가능
- 서버 이름은 hostname 이 자동으로 제목이 돼

## 필요한 것

- **systemd 가 있는 리눅스** (Ubuntu / Debian / Arch / CachyOS 등)
- `git`, `python3`(+venv), `curl`, `sudo`

처음 한 번만 설치해 두면 돼.

```bash
# Ubuntu / Debian
sudo apt install -y git python3 python3-venv curl

# Arch / CachyOS
sudo pacman -S --needed git python curl sudo
```

## 설치 (새 서버)

```bash
git clone https://github.com/rtokki764/Server-Dash.git
cd Server-Dash
bash install.sh
```

> fish 쉘이어도 `bash install.sh` 로 실행하면 돼. **명령은 한 줄씩** 입력해 줘 (여러 줄을 한 번에 붙여넣으면 꼬여).

`install.sh` 가 해 주는 일:
1. 필요한 것(python3, venv, systemd) 확인 — 없으면 뭘 설치해야 하는지 알려 주고 멈춤
2. 파이썬 가상환경 + 라이브러리 설치
3. `config.yaml` / `secrets.yaml` 이 **없을 때만** 예시 파일로 만듦 (있으면 안 건드림)
4. 사용자 서비스(`systemctl --user`)로 등록하고 시작 (로그아웃해도 계속 돎)
5. 열린 포트의 프로그램 이름을 보려고 `ss -tlnpH` **한 명령만** 비밀번호 없이 sudo 허용

끝에 `API 응답 코드: 200` 이 나오면 성공이야. 브라우저에서 http://127.0.0.1:5070 으로 열어 봐.

설치 위치는 어디든 괜찮아 (서비스 파일에 실제 경로가 자동으로 들어가).

### 설치 직후 할 일

```bash
nano config.yaml                              # services: 에 이 서버에서 볼 서비스 적기
systemctl --user restart bc250-dash.service   # 고친 뒤 다시 시작
```

알림(Mattermost / Telegram)을 쓸 거면 `nano secrets.yaml` 에 웹훅 주소를 채우고 마찬가지로 재시작해.

## 업데이트

이미 이 방식(git clone)으로 설치돼 있다면 이 두 줄이면 끝이야.

```bash
cd Server-Dash          # 설치한 폴더 (BC250 은 ~/bc250-dash)
git pull
bash install.sh
```

- `config.yaml`, `secrets.yaml` 은 git 에 안 올라가서 그대로 유지돼
- 화면 아래쪽 버전이 바뀌었는지 확인해 (브라우저에서 `Ctrl+F5`)
- `git pull` 이 "local changes" 라며 막으면 코드를 직접 고친 거야. `git stash` 후 다시 `git pull` 해 줘

## zip 으로 설치했던 서버를 git 으로 갈아타기 (한 번만)

예전에 zip 으로 설치한 서버(예: `~/bc250-dash`)를 git 방식으로 바꾸는 방법이야. 설정은 백업해서 그대로 가져와.

```bash
cp ~/bc250-dash/config.yaml ~/config-backup.yaml
cp ~/bc250-dash/secrets.yaml ~/secrets-backup.yaml
cd ~
mv bc250-dash bc250-dash-old
git clone https://github.com/rtokki764/Server-Dash.git bc250-dash
cp ~/config-backup.yaml ~/bc250-dash/config.yaml
cp ~/secrets-backup.yaml ~/bc250-dash/secrets.yaml
cd bc250-dash
bash install.sh
```

잘 도는 걸 확인하면 `rm -rf ~/bc250-dash-old` 로 옛 폴더를 지워도 돼.
이후 업데이트는 위의 **업데이트** 두 줄만 하면 돼.

## 문제가 생기면

| 증상 | 해결 |
|---|---|
| `python3-venv` 가 없다고 나옴 | `sudo apt install -y python3-venv` 후 다시 `bash install.sh` |
| 응답 코드가 200 이 아님 | `journalctl --user -u bc250-dash.service -n 30 --no-pager` 로 에러 확인 |
| 서비스가 안 보임 | `config.yaml` 의 `services:` 들여쓰기(스페이스 2칸) 확인 |
| 재부팅하면 꺼짐 | `sudo loginctl enable-linger $USER` |
| 화면이 안 바뀜 | 브라우저 `Ctrl+F5` (폰은 앱을 닫았다 다시 열기) |

## 삭제

```bash
systemctl --user disable --now bc250-dash.service bc250-mcp-watchdog.timer
rm -f ~/.config/systemd/user/bc250-dash.service ~/.config/systemd/user/bc250-mcp-watchdog.*
sudo rm -f /etc/sudoers.d/bc250-dash
rm -rf ~/Server-Dash        # 설치한 폴더
```

## 설정 (`config.yaml`)

처음엔 최소 설정이 들어 있어. `services:` 에 보고 싶은 서비스를 적으면 돼.

```yaml
services:
  - name: nginx
    unit: nginx.service     # 생략하면 <name>.service
    scope: system           # system(기본) / user / manual
    port: 80
```

- `scope: user` 는 `systemctl --user` 서비스, `manual` 은 systemd 가 아닌 프로그램(포트로만 확인)
- 이 서버에서 못 읽는 값(GPU·APU 온도·전력·팬 등)은 "—" 로 채우지 않고 **칸 자체를 숨겨**. 없는 걸 있는 것처럼 보여 주지 않아
- 센서: AMD APU/GPU(BC250 등)는 `sensors:` 를 안 적어도 **자동으로 찾아** (APU·GPU 온도, 전력 등). 칩이 다르면 `sensors:` 로 덮어써
- **Agent 탭**의 "MCP 서버" 줄과 "자동 복구" 줄은 `config.yaml` 에 `watchdog:`(mcp_name 등)을 적은 서버에서만 나와. 안 적으면 그 줄은 아예 안 보여 (가짜로 "연결됨"을 띄우지 않아).
- **Agent 탭**은 Hermes Agent 를 돌리는 서버에서만 나와. `config.yaml` 에 `hermes:` (unit 포함)를 적어야 켜져 — `config.example.yaml` 맨 아래 예시 참고
- 화면의 **목록에 없는 포트**에는 서비스 이름(systemd 서비스면 그 이름, 아니면 프로그램 이름)이 나와. Hermes 서비스로 보이면 **[🤖 Agent 등록]** 버튼이 떠서, 누르면 설정 파일 수정 없이 바로 Agent 탭에 연결돼 (`agent_extra.json` 에 저장)
- 버튼은 두 가지만 기억하면 돼. **목록+** = 서비스 목록에 올리기, **목록외 +** = 목록에서 치워서 "목록에 없는 서비스" 칸으로 보내기
  - 목록에 없는 **내부 전용 포트**도 **목록외 +** 로 치울 수 있어 (칩의 ↩ 로 되돌리기, `ports_moved.json`)
  - 이미 목록에 올라와 있는 서비스도 **목록외 +** 로 치울 수 있어. config.yaml 에 적은 것도 돼 (파일은 안 건드려, `services_hidden.json` 에 저장). 치우면 상태 감시·알림에서도 빠지고, 칩의 **목록+** 로 되돌려
- 알림: 대시보드 🔔 칸에서 **알림 켜기 · 빨강/노랑 대기 시간 · 쿨다운 · 해결 알림 · 링크 · 조용한 시간(시간대 포함) · 음소거**를 전부 바로 바꿀 수 있어 (`notify_settings.json` 에 저장, 재시작 필요 없음). `config.yaml` 의 `notify:` 는 처음 기본값일 뿐이야
- 경고 기준은 `thresholds:` 로 바꿀 수 있어 (기본: 메모리 85/95%, 디스크 80/90% 등)
- `dashboard.title` 을 비워 두면 hostname 이 제목이야

알림 받을 곳(웹훅 주소·토큰)은 `config.yaml` 에 적지 않아. **대시보드 🔔 알림 칸 → 받을 곳 → [+ 받을 곳 추가]** 에서 종류(Mattermost / Slack / Discord / Telegram / ntfy / 일반 웹훅)를 고르고 주소를 붙여넣으면 끝이야.
- 여러 개 추가할 수 있고, 하나씩 켜고 끄기·**테스트**·수정·삭제가 돼. 저장하면 바로 적용 (재시작 X)
- 주소·토큰은 서버의 `notify_channels.json` 에만 저장되고(나만 읽게 600 권한, 깃에는 안 올라감) 화면에 다시 보여 주지 않아
- 주소·토큰은 안전한 접속에서만 저장돼: **HTTPS** / **서버 자기 자신(127.0.0.1)** / **같은 사내망·집 네트워크(192.168.x.x 등)나 Tailscale** 에서 직접 열었을 때. 인터넷 쪽에서 http 로 열면 막아
  - 그래도 허용하려면 `config.yaml` 의 `notify:` 아래에 `allow_insecure_edit: true`
- 예전 방식(`secrets.yaml` 에 mattermost / telegram 적기)도 그대로 읽어. 화면에는 "secrets.yaml" 표시로 나오고 끄기만 할 수 있어

## 서비스 제어 (관리 버튼)

서비스 줄의 **[관리]** → dialog 에서 5개 동작을 실행해.

- user 서비스는 `systemctl --user` 로 바로 실행돼
- system 서비스는 그냥 시도 → `sudo -n` → 안 되면 **비밀번호 입력창**이 떠서 `sudo -S` 로 재시도
- 비밀번호는 그 요청 한 번에만 쓰고 **저장하지 않아** (로그에도 안 남음). 3번 틀리면 5분 잠금. HTTPS(또는 localhost)에서만 입력 가능
- 허용하는 동작은 5개뿐이고, 설정에 등록된 서비스만 제어할 수 있어
- 끄려면 `config.yaml` 의 `control: enabled: false`

## 보안 — 인터넷에 열 때 꼭 읽어 줘

이 대시보드는 서비스를 끄고 켤 수 있어. 기본값은 `127.0.0.1` (자기 자신만 접속)이야.

밖에서 보려면 **로그인 보호가 먼저** 있어야 해 (Cloudflare Access, nginx basic auth 등).
보호 없이 열 거라면 `control: enabled: false` 로 제어 기능을 꺼 줘.

## 다른 서버에 붙이기

`service_control.py` 와 `static/service_control.js` 는 이 대시보드 없이도 쓸 수 있어.
Flask 앱에 이렇게 붙이면 돼.

```python
from service_control import make_service_control
app.register_blueprint(make_service_control(get_services, cfg))
```

`get_services` 는 `[{"name":..., "unit":..., "scope": "system"|"user"}, ...]` 를 돌려주는 함수야.
화면에서는 `SC_OPEN("서비스이름")` 을 부르면 관리 dialog 가 열려.

## 파일

| 파일 | 역할 |
|---|---|
| `app.py` | 대시보드 본체 (수집 + API + 화면) |
| `templates/index.html` | 화면 (PC·모바일) |
| `service_control.py`, `static/service_control.js` | 서비스 제어 API 와 dialog |
| `notify.py` | Mattermost / Telegram 알림 |
| `config.example.yaml`, `secrets.example.yaml` | 설정 예시 (`secrets.yaml` 은 옛 방식이라 선택) |
| `install.sh` | 설치·업데이트 스크립트 |
| `VERSION` | 현재 버전 (화면 아래에 표시) |
| `demo.py` | 가짜 데이터로 화면만 확인 (`BC250_DEMO=1 python app.py`) |
| `mcp_watchdog.py` | (선택) Hermes 에이전트용 자동 복구. 설정이 없으면 아무것도 안 함 |
