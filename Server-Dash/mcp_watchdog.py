#!/usr/bin/env python3
"""
mypc 자동 복구 (5분마다 systemd 타이머가 실행)

mypc가 재시작되면 hermes는 예전 세션 번호를 계속 들고 와서 404를 받고 parked 상태로 멈춰.
그럴 때 hermes-gateway를 재시작해서 새 세션으로 다시 연결시키는 스크립트야.

아래 조건이 "모두" 맞을 때만 재시작해:
  1. mypc가 parked_minutes(10분) 넘게 parked
  2. PC의 mypc 서버는 살아 있음 (꺼져 있으면 재시작해도 소용없음)
  3. 봇이 처리 중인 작업이 없음 (대화 도중에 끊지 않게)
  4. 마지막 자동 재시작 후 cooldown_minutes(30분) 지남 (무한 반복 방지)

한 일은 watchdog.log 에 남고, 대시보드 Hermes 탭에서도 보여.
"""
import json
import subprocess
import time
from datetime import datetime

import app   # 대시보드의 설정과 도우미 함수를 그대로 씀

W = app.CFG.get("watchdog") or {}
H = app.CFG.get("hermes") or {}
if not W or not H.get("unit"):
    # watchdog/hermes 설정이 없는 서버: 할 일이 없으니 정상 종료 (타이머가 있어도 에러 안 남)
    print("watchdog/hermes 설정이 없어서 아무것도 안 함")
    raise SystemExit(0)
LOG = app.BASE / "watchdog.log"
STATE = app.BASE / "watchdog.state.json"


def log(msg, keep=True):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)                 # journalctl 에 남음
    if keep:                                # 실제로 한 일만 파일에 남김
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        # 파일이 너무 커지지 않게 최근 200줄만 유지
        lines = LOG.read_text(encoding="utf-8").splitlines()
        if len(lines) > 200:
            LOG.write_text("\n".join(lines[-200:]) + "\n", encoding="utf-8")


def main():
    now = time.time()
    hz = app.read_hermes()
    m = next((x for x in hz.get("mcp", []) if x["name"] == W["mcp_name"]), None)

    # 1. parked 인가?
    if not m or m["state"] == "connected":
        print(f"{W['mcp_name']}: 정상 (또는 게이트웨이 시작 후 상태 변화 없음)")
        return
    minutes = (now - m["at"]) / 60
    if minutes < W["parked_minutes"]:
        print(f"{W['mcp_name']}: {m['state']} {minutes:.0f}분째 — {W['parked_minutes']}분까지 기다림")
        return

    # 2. PC 쪽 서버가 살아 있나? (토큰 없이 보내면 401 이 와야 정상)
    code, _ = app.http_get(W["check_url"], timeout=5)
    if code is None:
        print(f"PC 쪽 {W['mcp_name']} 서버 응답 없음 — PC가 꺼졌거나 서버가 죽음. 재시작 안 함")
        return

    # 3. 봇이 일하는 중인가?
    active = (hz.get("gateway") or {}).get("active_agents") or 0
    if active:
        print(f"봇이 작업 {active}개 처리 중 — 다음 차례에 다시 확인")
        return

    # 4. 최근에 이미 재시작했나?
    try:
        last = json.loads(STATE.read_text())["last_restart"]
    except Exception:
        last = 0
    if now - last < W["cooldown_minutes"] * 60:
        print(f"최근 {int((now - last) / 60)}분 전에 이미 재시작함 — 쿨다운 중")
        return

    # 조건 모두 충족 → 재시작
    scope = ["--user"] if H["scope"] == "user" else []
    r = subprocess.run(["systemctl", *scope, "restart", H["unit"]], capture_output=True, text=True)
    STATE.write_text(json.dumps({"last_restart": now}))
    if r.returncode == 0:
        log(f"{W['mcp_name']} {minutes:.0f}분째 {m['state']} → {H['unit']} 재시작함")
    else:
        log(f"{W['mcp_name']} 복구용 재시작 실패: {r.stderr.strip()[:200]}")


if __name__ == "__main__":
    main()
