# Server-Dash

리눅스(systemd) 서버의 상태를 한 화면에서 보는 가벼운 웹 대시보드야.
CPU·메모리·디스크·온도, 서비스 상태, 열린 포트를 보여주고, 문제가 생기면 Mattermost / Telegram 으로 알려줘.
서비스 줄의 **[관리]** 버튼으로 `start / stop / restart / enable / disable` 도 할 수 있어.

- Python + Flask 한 개짜리 앱 (DB 없음)
- 폰·PC 둘 다 쓰기 편한 화면, 홈 화면에 앱으로 설치 가능
- 서버 이름은 hostname 이 자동으로 제목이 돼

## 설치

```bash
git clone https://github.com/rtokki764/Server-Dash.git
cd Server-Dash
bash install.sh
```

`install.sh` 가 해 주는 일:
1. 파이썬 가상환경 + 라이브러리 설치
2. `config.yaml` 이 없으면 `config.example.yaml` 을 복사해서 만듦
3. 사용자 서비스(`systemctl --user`)로 등록하고 시작 (로그아웃해도 계속 돎)
4. 열린 포트의 프로그램 이름을 보려고 `ss -tlnpH` **한 명령만** 비밀번호 없이 sudo 허용

끝나면 http://127.0.0.1:5070 으로 열어 봐.

> 설치 위치는 어디든 괜찮아. 필요한 건 systemd 가 있는 리눅스와 Python 3 뿐이야.

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
- 센서(`sensors:`)·알림(`notify:`) 같은 건 선택이야. 없으면 그 부분만 빠져
- 경고 기준은 `thresholds:` 로 바꿀 수 있어 (기본: 메모리 85/95%, 디스크 80/90% 등)
- `dashboard.title` 을 비워 두면 hostname 이 제목이야

알림 받을 곳(웹훅 주소·토큰)은 `config.yaml` 이 아니라 **`secrets.yaml`** 에 적어 (`secrets.example.yaml` 참고).

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
| `config.example.yaml`, `secrets.example.yaml` | 설정 예시 |
| `install.sh` | 설치·업데이트 스크립트 |
| `demo.py` | 가짜 데이터로 화면만 확인 (`BC250_DEMO=1 python app.py`) |
| `mcp_watchdog.py` | (선택) Hermes 에이전트용 자동 복구. 설정이 없으면 아무것도 안 함 |

## 업데이트

```bash
cd Server-Dash && git pull && bash install.sh
```

`config.yaml` 과 `secrets.yaml` 은 git 에 안 올라가니까 업데이트해도 그대로야.
