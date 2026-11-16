#!/usr/bin/env python3
"""
BC250 서버 대시보드
- 백그라운드 스레드가 몇 초마다 서버 상태를 모아 두고
- Flask는 모아 둔 값을 /api/status, /api/history 로 돌려주기만 해
  (그래서 화면을 여러 개 열어도 서버에 부담이 거의 없어)

실행:  python app.py            (진짜 데이터)
       BC250_DEMO=1 python app.py  (가짜 데이터로 화면만 확인)
"""
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
from collections import deque
from glob import glob
from pathlib import Path

import psutil
import yaml
from flask import Flask, jsonify, render_template, request, send_from_directory

from notify import Notifier
from service_control import make_service_control

BASE = Path(__file__).resolve().parent
CFG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))
DEMO = os.environ.get("BC250_DEMO") == "1"

D = CFG.get("dashboard") or {}
# 경고 기준: config에 없으면 무난한 기본값
_TH_DEFAULT = {
    "apu_temp_c": {"warn": 80, "bad": 90}, "gpu_temp_c": {"warn": 80, "bad": 90},
    "mem_pct": {"warn": 85, "bad": 95}, "swap_pct": {"warn": 70, "bad": 90},
    "disk_pct": {"warn": 80, "bad": 90},
    "telegram_reconnects_per_hour": {"warn": 3, "bad": 20},
}
TH = {**_TH_DEFAULT, **(CFG.get("thresholds") or {})}
SENS = CFG.get("sensors") or {}
# 제목: config의 title이 비어 있으면 서버 hostname을 그대로 씀 → 어느 서버든 복사해서 사용 가능
TITLE = (D.get("title") or "").strip() or socket.gethostname()

# "인지함"을 누른 실패 서비스 목록 (대시보드가 알아서 읽고 씀)
ACK_FILE = BASE / "ack.json"
ACK_LOCK = threading.Lock()

# 웹훅 알림 (설정: config.yaml 의 notify, 주소/토큰: secrets.yaml)
NOTIFY = Notifier(BASE, CFG.get("notify"), title=TITLE.split()[0])

# 화면에서 [목록에 추가]한 서비스 (config.yaml 목록 뒤에 붙음, zip 설치해도 안 지워짐)
EXTRA_FILE = BASE / "services_extra.json"
# 처음 본 서비스·포트 기록 ("🆕 새로 생긴 것" 판단용)
SEEN_FILE = BASE / "seen.json"
EXTRA_LOCK = threading.Lock()


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_extras():
    return [s for s in _read_json(EXTRA_FILE, []) if isinstance(s, dict) and s.get("name")]


def all_services():
    """config.yaml 서비스 + 화면에서 추가한 서비스"""
    out = []
    for s in list(CFG.get("services") or []) + [{**s, "extra": True} for s in load_extras()]:
        s = dict(s)
        s.setdefault("scope", "system")
        if s["scope"] in ("system", "user"):
            s.setdefault("unit", s["name"] if s["name"].endswith(".service") else s["name"] + ".service")
        out.append(s)
    return out


def proc_owner(pid):
    """그 프로세스가 어느 systemd 서비스 소속인지 /proc/<pid>/cgroup 으로 알아내기
    → {"unit": "x.service", "scope": "system"|"user"} 또는 None (직접 띄운 프로그램)"""
    try:
        path = Path(f"/proc/{pid}/cgroup").read_text().strip().splitlines()[-1].split("::", 1)[-1]
    except Exception:
        return None
    parts = path.split("/")
    if any(p.startswith("user@") for p in parts):
        units = [p for p in parts if p.endswith(".service") and not p.startswith("user@")]
        return {"unit": units[-1], "scope": "user"} if units else None
    units = [p for p in parts if p.endswith(".service")]
    return {"unit": units[-1], "scope": "system"} if units else None


def suggest_for(unit=None, user=False, port=None, proc=None):
    """[목록에 추가] 창에 미리 채울 값"""
    if unit:
        return {"name": unit.removesuffix(".service"), "unit": unit, "scope": "user" if user else "system", "port": port}
    return {"name": proc or f"port-{port}", "unit": None, "scope": "manual", "port": port, "process": proc}


# 열린 포트 중 "원래 있는 것"이라 미확인으로 치지 않을 프로세스
KNOWN_SYSTEM_PROCS = {"systemd-resolve", "cloudflared", "avahi-daemon"} | set(CFG.get("known_processes") or [])


# ─────────────────────────────────────────────
#  작은 도우미 함수들
# ─────────────────────────────────────────────
def run(cmd, timeout=5):
    """명령을 실행하고 출력 문자열을 돌려줘. 실패하면 빈 문자열."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def read_num(path):
    try:
        return float(Path(path).read_text().strip())
    except Exception:
        return None


def http_get(url, timeout=2):
    """(상태코드, 본문) 돌려줌. 연결 실패면 (None, '')"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bc250-dash"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return None, ""


def level(value, rule):
    """값을 기준과 비교해서 ok / warn / bad"""
    if value is None or rule is None:
        return "ok"
    if value >= rule["bad"]:
        return "bad"
    if value >= rule["warn"]:
        return "warn"
    return "ok"


# ─────────────────────────────────────────────
#  하드웨어 센서 (/sys/class/hwmon 에서 직접 읽기)
# ─────────────────────────────────────────────
def hwmon(chip, label):
    """sensors 명령에 나오는 '칩 이름 + 라벨'로 값을 찾아.
    온도 → °C, 팬 → RPM, 전력 → W 로 바꿔서 돌려줘."""
    for d in glob("/sys/class/hwmon/hwmon*"):
        try:
            if Path(d, "name").read_text().strip() != chip:
                continue
        except Exception:
            continue
        for lf in glob(f"{d}/*_label"):
            try:
                if Path(lf).read_text().strip() != label:
                    continue
            except Exception:
                continue
            prefix = lf[: -len("_label")]          # 예: .../temp1
            kind = Path(prefix).name.rstrip("0123456789")
            for suffix in ("_input", "_average"):
                v = read_num(prefix + suffix)
                if v is not None:
                    if kind == "temp":
                        return round(v / 1000, 1)
                    if kind == "power":
                        return round(v / 1_000_000, 1)
                    return v
    return None


_gpu_prev = {}   # GPU 클라이언트별 (시각, 누적 사용 ns)


def gpu_busy_fdinfo():
    """gpu_busy_percent를 지원 안 하는 BC250용 대체 방법.
    프로그램마다 GPU를 쓴 누적 시간(ns)이 /proc/<pid>/fdinfo 에 적혀 있어서
    지난번 값과의 차이 ÷ 지난 시간 = 사용률(%)"""
    now, clients = time.monotonic(), {}
    for fd in glob("/proc/[0-9]*/fd/*"):
        try:
            if not os.readlink(fd).startswith("/dev/dri/"):
                continue
            pid, num = fd.split("/")[2], fd.rsplit("/", 1)[1]
            txt = Path(f"/proc/{pid}/fdinfo/{num}").read_text()
        except OSError:
            continue        # 다른 사용자 프로세스는 못 읽음 → 건너뛰기
        cid = re.search(r"^drm-client-id:\s*(\d+)", txt, re.M)
        gfx = re.search(r"^drm-engine-gfx:\s*(\d+)\s*ns", txt, re.M)
        if cid and gfx:
            clients[cid.group(1)] = int(gfx.group(1))
    busy_ns, dt = 0, None
    for cid, ns in clients.items():
        if cid in _gpu_prev and ns >= _gpu_prev[cid][1]:
            busy_ns += ns - _gpu_prev[cid][1]
            dt = now - _gpu_prev[cid][0]
    _gpu_prev.clear()
    _gpu_prev.update({cid: (now, ns) for cid, ns in clients.items()})
    if not dt:
        return None         # 첫 번째 호출은 비교할 값이 없음
    return round(min(100.0, busy_ns / (dt * 1e9) * 100), 1)


def gpu_clock_mhz():
    """pp_dpm_sclk 에서 * 표시된 현재 클럭"""
    try:
        path = Path(SENS["gpu_busy"]).with_name("pp_dpm_sclk")
        for line in path.read_text().splitlines():
            if line.strip().endswith("*"):
                return int(re.search(r"(\d+)\s*Mhz", line, re.I).group(1))
    except Exception:
        pass
    return None


def primary_ip():
    """외부로 나갈 때 쓰는 대표 IP (패킷은 안 보냄). 못 찾으면 빈 문자열"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def read_hardware():
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    dk = psutil.disk_usage("/")
    gb = 1024 ** 3
    def _hw(key):          # 설정에 없는 센서는 그냥 None (다른 서버에서도 돌게)
        c = SENS.get(key)
        return hwmon(**c) if c else None
    vram_u, vram_t = read_num(SENS.get("vram_used")), read_num(SENS.get("vram_total"))
    gtt_u, gtt_t = read_num(SENS.get("gtt_used")), read_num(SENS.get("gtt_total"))
    return {
        "cpu_pct": psutil.cpu_percent(interval=None),
        "gpu_pct": read_num(SENS.get("gpu_busy")) if read_num(SENS.get("gpu_busy")) is not None else gpu_busy_fdinfo(),
        "gpu_mhz": gpu_clock_mhz(),
        "apu_temp": _hw("apu_temp"),
        "gpu_temp": _hw("gpu_temp"),
        "apu_power": _hw("apu_power"),
        "pump_rpm": _hw("pump_fan"),
        "mem_used": round(vm.used / gb, 2), "mem_total": round(vm.total / gb, 2), "mem_pct": vm.percent,
        "swap_used": round(sw.used / gb, 2), "swap_total": round(sw.total / gb, 2), "swap_pct": sw.percent,
        "disk_used": round(dk.used / gb, 1), "disk_total": round(dk.total / gb, 1), "disk_pct": dk.percent,
        "vram_used": round(vram_u / gb, 2) if vram_u else None,
        "vram_total": round(vram_t / gb, 2) if vram_t else None,
        "gtt_used": round(gtt_u / gb, 2) if gtt_u else None,
        "gtt_total": round(gtt_t / gb, 2) if gtt_t else None,
    }


# ─────────────────────────────────────────────
#  llama-server (health + 생성 속도)
# ─────────────────────────────────────────────
_llama_prev = {}   # 포트별 (시각, 누적 생성 토큰) → 속도 계산용


def read_llama(port):
    code, _ = http_get(f"http://127.0.0.1:{port}/health", timeout=1)
    info = {"healthy": code == 200, "tps": None, "busy": None}
    # /slots: 지금 답을 만드는 중인지
    code, body = http_get(f"http://127.0.0.1:{port}/slots", timeout=1)
    if code == 200:
        try:
            info["busy"] = any(s.get("is_processing") for s in json.loads(body))
        except Exception:
            pass
    # /metrics: llama-server를 --metrics 옵션으로 켜야 나옴
    code, body = http_get(f"http://127.0.0.1:{port}/metrics", timeout=1)
    if code == 200:
        m = re.search(r"^llamacpp:tokens_predicted_total\s+([\d.e+]+)", body, re.M)
        if m:
            now, total = time.time(), float(m.group(1))
            prev = _llama_prev.get(port)
            if prev and now > prev[0] and total >= prev[1]:
                info["tps"] = round((total - prev[1]) / (now - prev[0]), 1)
            _llama_prev[port] = (now, total)
    return info


# ─────────────────────────────────────────────
#  systemd 서비스
# ─────────────────────────────────────────────
def systemd_show(units, user):
    """여러 서비스 상태를 한 번에 조회"""
    if not units:
        return {}
    cmd = ["systemctl"] + (["--user"] if user else []) + [
        "show", "--timestamp=unix",
        "-p", "Id,ActiveState,SubState,ActiveEnterTimestamp,StateChangeTimestamp,NRestarts,MainPID", *units]
    out, res = run(cmd), {}
    for block in out.strip().split("\n\n"):
        kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if "Id" not in kv:
            continue
        ts = kv.get("ActiveEnterTimestamp", "")
        since = int(ts[1:]) if ts.startswith("@") and ts[1:].isdigit() else None
        ch = kv.get("StateChangeTimestamp", "")
        changed = int(ch[1:]) if ch.startswith("@") and ch[1:].isdigit() else None
        res[kv["Id"]] = {
            "active": kv.get("ActiveState"), "sub": kv.get("SubState"),
            "since": since if kv.get("ActiveState") == "active" else None,
            "changed": changed,     # 마지막으로 상태가 바뀐 시각 (실패한 서비스면 = 실패한 시각)
            "restarts": int(kv["NRestarts"]) if kv.get("NRestarts", "").isdigit() else None,
            "pid": int(kv["MainPID"]) if kv.get("MainPID", "0").isdigit() and kv.get("MainPID") != "0" else None,
        }
    return res


def systemd_list(user, state):
    cmd = ["systemctl"] + (["--user"] if user else []) + [
        "list-units", "--type=service", f"--state={state}", "--no-legend", "--plain", "--no-pager"]
    names = []
    for line in run(cmd).splitlines():
        parts = line.split(None, 4)
        if parts:
            names.append({"unit": parts[0], "desc": parts[4] if len(parts) > 4 else "", "user": user})
    return names


# ─────────────────────────────────────────────
#  열린 포트 (ss)
# ─────────────────────────────────────────────
def read_ports():
    """{포트: {addr, exposure, proc, pid}}
    sudo로 실행해야 다른 사용자 프로세스 이름까지 보여서 sudo -n 먼저 시도"""
    out = run(["sudo", "-n", "/usr/bin/ss", "-tlnpH"]) or run(["ss", "-tlnpH"])
    ports = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        local = f[3]
        host, _, port = local.rpartition(":")
        if not port.isdigit():
            continue
        port = int(port)
        host = host.strip("[]").split("%")[0]
        internal = host.startswith("127.") or host == "::1"
        m = re.search(r'\("([^"]+)",pid=(\d+)', line)
        cur = ports.get(port)
        entry = {"addr": host, "exposure": "internal" if internal else "lan",
                 "proc": m.group(1) if m else None, "pid": int(m.group(2)) if m else None}
        # 같은 포트가 IPv4/IPv6 둘 다 열려 있으면 더 넓게 열린 쪽으로
        if cur is None or (cur["exposure"] == "internal" and entry["exposure"] == "lan"):
            if cur and not entry["proc"]:
                entry["proc"], entry["pid"] = cur["proc"], cur["pid"]
            ports[port] = entry
    return ports


# ─────────────────────────────────────────────
#  서브도메인 / 원격 서비스 확인 (느리니까 1분마다)
# ─────────────────────────────────────────────
_url_fails = {}   # 주소별 연속 실패 횟수


def check_url(url):
    code, _ = http_get(url, timeout=8)
    # Access 로그인 페이지로 넘어가는 302/403도 "살아 있음"으로 봐
    ok = code is not None and code < 500
    _url_fails[url] = 0 if ok else _url_fails.get(url, 0) + 1
    # 한 번 실패는 흔하니까 3번 연속 실패해야 "안 됨"으로 표시
    return {"ok": ok or _url_fails[url] < 3, "code": code, "fails": _url_fails[url]}


# ─────────────────────────────────────────────
#  Hermes 로그 분석 (30초마다)
# ─────────────────────────────────────────────
def _ts(v):
    """숫자(epoch)든 ISO 문자열이든 epoch 초로 바꿔"""
    if v is None:
        return None
    try:
        f = float(v)
        return f / 1000 if f > 1e12 else f
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def read_hermes_state():
    """gateway_state.json (봇별 연결 상태) + state.db (봇별 마지막 활동)"""
    H = CFG.get("hermes") or {}
    home = Path(os.path.expanduser(H.get("home", "~/.hermes")))
    meta = H.get("bots", {})
    out = {"gateway": None, "bots": []}
    try:
        gs = json.loads((home / "gateway_state.json").read_text())
    except Exception:
        return out
    hb = _ts(gs.get("updated_at"))
    out["gateway"] = {"state": gs.get("gateway_state"), "heartbeat": hb,
                      "active_agents": gs.get("active_agents"), "version": gs.get("code_version")}
    served = gs.get("served_profiles") or []
    parked = set((gs.get("parked_profiles") or {}).keys())

    # 플랫폼: "thia:mattermost" → thia, 접두사 없으면 default
    plats = {}
    for key, v in (gs.get("platforms") or {}).items():
        prof, _, plat = key.rpartition(":")
        prof = prof or "default"
        st = v.get("state")
        lv = "ok" if st == "connected" and not v.get("needs_attention") else (
             "warn" if st in ("connecting", "retrying", "reconnecting") or v.get("retrying_since") else "bad")
        plats.setdefault(prof, []).append({"name": plat.capitalize(), "state": st, "level": lv,
                                           "error": v.get("error_message")})

    # 봇마다 자기 state.db 를 따로 씀: default → ~/.hermes, 나머지 → ~/.hermes/profiles/<이름>
    def prof_dir(prof):
        return home if prof == "default" else home / "profiles" / prof

    def read_db(prof):
        info = {"last": None, "model": None, "today": 0}
        db = prof_dir(prof) / "state.db"
        if not db.exists():
            return info
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            for model, last, started in con.execute(
                    "select model, last_activity_at, started_at from sessions "
                    "where coalesce(profile_name, 'default') = ? or ? != 'default'", (prof, prof)):
                t = _ts(last) or _ts(started)
                if t and (info["last"] is None or t > info["last"]):
                    info["last"], info["model"] = t, model
            midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
            for (ts,) in con.execute(
                    "select m.timestamp from messages m join sessions s on m.session_id = s.id "
                    "where m.role = 'user' and m.id > (select coalesce(max(id), 0) - 5000 from messages)"
                    + ("" if prof != "default" else " and coalesce(s.profile_name, 'default') = 'default'")):
                t = _ts(ts)
                if t and t >= midnight:
                    info["today"] += 1
        finally:
            con.close()
        return info

    def config_model(prof):
        """봇 config.yaml 에 설정된 모델 (형식이 여러 가지라 흔한 모양을 다 받아줌)"""
        try:
            c = yaml.safe_load((prof_dir(prof) / "config.yaml").read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        m = c.get("model")
        if isinstance(m, str):
            return m
        if isinstance(m, dict):
            for k in ("default", "model", "name", "id"):
                if isinstance(m.get(k), str):
                    return m[k]
        for k in ("default_model", "llm_model"):
            if isinstance(c.get(k), str):
                return c[k]
        return None

    acts = {}
    for prof in set(served) | set(meta.keys()):
        try:
            acts[prof] = read_db(prof)
        except Exception as e:
            out.setdefault("db_error", {})[prof] = repr(e)
        cm = config_model(prof)
        if cm and (cm.startswith(("/", "~")) or ":\\" in cm):
            cm = re.split(r"[\\/]", cm)[-1].removesuffix(".gguf")   # 모델 파일 경로면 파일 이름만
        if cm:
            acts.setdefault(prof, {})["model"] = cm

    order = [k for k in meta if not served or k in served] + [k for k in served if k not in meta]
    for prof in order:          # config.yaml 에 적은 순서대로 (Thia → Tessa → Dek → 기본)
        m = meta.get(prof, {})
        pl = plats.get(prof, [])
        a = acts.get(prof, {})
        level = "bad" if prof in parked else max((p["level"] for p in pl), key=["ok", "warn", "bad"].index, default="ok")
        out["bots"].append({"id": prof, "name": m.get("name", prof), "role": m.get("role"),
                            "mm_user": m.get("mattermost"), "tg_user": str(m.get("telegram") or "").lstrip("@") or None,
                            "parked": prof in parked, "level": level, "platforms": pl,
                            "last": a.get("last"), "model": a.get("model"), "today": a.get("today", 0)})
    return out


def read_hermes():
    H = CFG.get("hermes") or {}
    if not H.get("unit"):      # hermes 섹션이 없는 서버 → Agent 탭만 비워 두고 정상 동작
        return {"platforms": [], "mcp": [], "groups": [], "tail": [], "window_h": 0,
                "gateway_since": 0, "watchdog": [], "gateway": None, "bots": []}
    since = f"-{H.get('log_window_hours', 6)}h"
    cmd = ["journalctl"] + (["--user"] if H.get("scope", "user") == "user" else []) + [
        "-u", H["unit"], "--since", since, "-o", "json", "--no-pager"]
    lines = []
    for raw in run(cmd, timeout=10).splitlines():
        try:
            j = json.loads(raw)
            msg = j.get("MESSAGE", "")
            if isinstance(msg, list):
                msg = bytes(msg).decode("utf-8", "replace")
            lines.append((int(j["__REALTIME_TIMESTAMP"]) / 1e6, msg))
        except Exception:
            continue
    now = time.time()

    # 메신저 연결 상태
    platforms = []
    for p in H.get("platforms") or []:
        pl = [(t, m) for t, m in lines if re.search(p["pattern"], m)]
        fails = [(t, m) for t, m in pl if p.get("fail") and p["fail"] in m]
        fails_1h = sum(1 for t, _ in fails if now - t < 3600)
        last = pl[-1] if pl else None
        if p.get("fail"):
            st = level(fails_1h, TH.get(f"{p['name'].lower()}_reconnects_per_hour"))
            # 마지막 기록이 실패이고 그 뒤로 복구가 없으면 끊긴 것
            if last and p["fail"] in last[1] and now - last[0] < 600:
                st = "bad" if st == "bad" else "warn"
        else:
            errs = [m for _, m in pl if re.search(r"\b(ERROR|CRITICAL)\b", m)]
            st = "warn" if errs else "ok"
        platforms.append({"name": p["name"], "state": st, "fails_1h": fails_1h,
                          "last": last[0] if last else None})

    # MCP 서버: 이름별 마지막 상태
    # MCP 상태는 "지금 돌고 있는 게이트웨이"가 시작된 뒤의 기록만 봐.
    # (재시작 전에 찍힌 parked 기록 때문에 계속 빨간불로 남지 않게)
    gw_since = systemd_show([H["unit"]], H.get("scope", "user") == "user").get(H["unit"], {}).get("since") or 0
    mcp = {}
    for t, m in lines:
        if t < gw_since:
            continue
        mm = re.search(H.get("mcp_pattern") or "$^", m)
        if mm:
            mcp[mm.group("name")] = {"state": mm.group("state"), "at": t}
    mcp_list = [{"name": k, "state": v["state"], "at": v["at"],
                 "level": "ok" if v["state"] == "connected" else ("warn" if v["state"] == "degraded" else "bad")}
                for k, v in sorted(mcp.items())]

    # 경고 묶음
    groups = []
    for g in H.get("warning_groups") or []:
        hits = [t for t, m in lines if re.search(g["pattern"], m)]
        if hits:
            groups.append({"label": g["label"], "count": len(hits), "last": hits[-1]})

    # 최근 로그 30줄 (앞의 중복 시각은 잘라내기)
    tail = []
    for t, m in lines[-30:]:
        m = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+\s+", "", m)
        lv = re.search(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b", m)
        tail.append({"t": t, "level": lv.group(1) if lv else "", "msg": m[:400]})

    # 자동 복구(mcp_watchdog.py) 기록 마지막 5줄
    watchdog = []
    try:
        for line in (BASE / "watchdog.log").read_text(encoding="utf-8").splitlines()[-5:]:
            ts, _, msg = line.partition(" ")
            watchdog.append({"t": _ts(ts), "msg": msg})
    except Exception:
        pass

    res = {"platforms": platforms, "mcp": mcp_list, "groups": groups, "tail": tail,
           "window_h": H.get("log_window_hours", 6), "gateway_since": gw_since, "watchdog": watchdog}
    res.update(read_hermes_state())
    return res


# ─────────────────────────────────────────────
#  서비스 표 만들기 (config + systemd + 포트)
# ─────────────────────────────────────────────
def build_services(ports, urlchecks, llama):
    svcs = all_services()
    sys_units = [s["unit"] for s in svcs if s.get("scope") == "system"]
    usr_units = [s["unit"] for s in svcs if s.get("scope") == "user"]
    st = {**systemd_show(sys_units, False), **systemd_show(usr_units, True)}
    rows, claimed = [], set()
    for s in svcs:
        port = s.get("port")
        p = ports.get(port) if port else None
        if port:
            claimed.add(port)
        if s.get("scope") == "manual":
            running = p is not None
            since = None
            if p and p.get("pid"):
                try:
                    since = int(psutil.Process(p["pid"]).create_time())
                except Exception:
                    pass
            info = {"active": "active" if running else "inactive", "since": since, "restarts": None}
        else:
            info = st.get(s["unit"], {"active": "unknown", "since": None, "restarts": None})
        state = {"active": "ok", "activating": "warn", "reloading": "warn"}.get(info["active"], "bad")
        if state == "bad" and s.get("optional"):
            state = "off"           # 일부러 꺼 두는 서비스 → 회색 "꺼짐", 경고 안 함
        if port and not p and state == "ok":
            state = "warn"          # 서비스는 켜졌는데 포트가 안 열림
        if s.get("health") and state == "ok":
            healthy = llama[port]["healthy"] if port in llama else http_get(s["health"], timeout=1)[0] == 200
            if not healthy:
                state = "warn"      # 서비스는 켜졌는데 응답이 없음
        uc = urlchecks.get(s.get("subdomain"))
        if uc and not uc["ok"] and state == "ok":
            state = "warn"          # 프로그램은 켜졌는데 서브도메인으로 접속이 안 됨
        if s.get("subdomain"):
            exposure = "internet"
        elif p:
            exposure = p["exposure"]
        else:
            exposure = None
        rows.append({
            "name": s["name"], "scope": s.get("scope"), "port": port,
            "model": s.get("model"), "used_by": s.get("used_by"), "note": s.get("note"),
            "subdomain": s.get("subdomain"), "access": s.get("access"),
            "exposure": exposure, "listening": p is not None if port else None,
            "state": state, "active": info["active"], "since": info["since"],
            "restarts": info["restarts"], "url": uc, "optional": bool(s.get("optional")), "extra": bool(s.get("extra")),
        })
    for r in CFG.get("remote", []):
        uc = urlchecks.get(r["url"])
        rows.append({
            "name": r["name"], "scope": "remote", "port": int(r["url"].rsplit(":", 1)[-1].split("/")[0]),
            "note": r.get("where"), "subdomain": r.get("subdomain"), "access": r.get("access"),
            "exposure": "internet",
            "state": "ok" if uc and uc["ok"] else ("off" if r.get("optional") else ("bad" if uc else "warn")),
            "active": None, "since": None, "restarts": None,
            "url": urlchecks.get(r.get("subdomain")),
        })

    unknown = [{"port": k, **v} for k, v in sorted(ports.items())
               if k not in claimed and v.get("proc") not in KNOWN_SYSTEM_PROCS]
    hidden = set(CFG.get("hidden_units") or [])
    pinned_units = set(sys_units) | hidden
    other_sys = [u for u in systemd_list(False, "running") if u["unit"] not in pinned_units]
    other_usr = [u for u in systemd_list(True, "running") if u["unit"] not in set(usr_units) | hidden]
    failed = [u for u in systemd_list(False, "failed") + systemd_list(True, "failed") if u["unit"] not in hidden]
    # 실패한 시각도 같이 (인지함을 누른 "그 실패"인지 구분하는 데 씀)
    for user in (False, True):
        units = [f["unit"] for f in failed if f.get("user") == user]
        info = systemd_show(units, user) if units else {}
        for f in failed:
            if f.get("user") == user:
                f["failed_at"] = info.get(f["unit"], {}).get("changed")
    for u in unknown:                   # 포트 연 프로그램이 어느 서비스 소속인지 + 추가용 기본값
        own = proc_owner(u["pid"]) if u.get("pid") else None
        u["owner"] = own
        u["suggest"] = suggest_for(own and own["unit"], own and own["scope"] == "user", u["port"], u.get("proc"))
    others = other_sys + other_usr
    for o in others:
        o["suggest"] = suggest_for(o["unit"], o.get("user"))
    return rows, unknown, others, failed


def detect_new(unknown, others, now):
    """처음 보는 서비스·사내망 포트 찾기. 처음 실행 때 있던 건 "원래 있던 것"으로 기록만 해.
    돌려주는 것: (새로 생긴 것 목록, 처리한(추가/넘기기) 이름표 모음)"""
    with EXTRA_LOCK:
        seen = _read_json(SEEN_FILE, {})
        units = {o["unit"]: o for o in others}
        lan = {u["port"]: u for u in unknown if u["exposure"] == "lan"}
        if not seen.get("init"):
            seen = {"init": now, "units": {k: now for k in units},
                    "ports": {str(p): {"proc": u.get("proc"), "t": now} for p, u in lan.items()}, "handled": {}}
            _write_json(SEEN_FILE, seen)
            return [], set()
        seen.setdefault("units", {}); seen.setdefault("ports", {}); seen.setdefault("handled", {})
        changed = False
        for k in units:
            if k not in seen["units"]:
                seen["units"][k] = now
                changed = True
        for p, u in lan.items():
            s = seen["ports"].get(str(p))
            if not s or s.get("proc") != u.get("proc"):        # 새 포트, 또는 같은 포트를 다른 프로그램이 엶
                seen["ports"][str(p)] = {"proc": u.get("proc"), "t": now}
                seen["handled"].pop(f"port:{p}", None)
                changed = True
        if changed:
            _write_json(SEEN_FILE, seen)
        handled, init = set(seen["handled"]), seen["init"]
    items, by_unit = [], {}
    for k, o in units.items():
        t = seen["units"][k]
        if t > init and f"svc:{k}" not in handled:
            it = {"kind": "svc", "key": f"svc:{k}", "unit": k, "user": o.get("user"), "desc": o.get("desc"),
                  "since": t, "port": None, "suggest": o["suggest"]}
            items.append(it)
            by_unit[k] = it
    for p, u in lan.items():
        s = seen["ports"][str(p)]
        if s["t"] <= init or f"port:{p}" in handled:
            continue
        own = u.get("owner")
        if own and own["unit"] in by_unit:              # 새 서비스가 연 포트 → 그 줄에 같이 표시
            by_unit[own["unit"]]["port"] = p
            by_unit[own["unit"]]["suggest"] = {**by_unit[own["unit"]]["suggest"], "port": p}
            continue
        items.append({"kind": "port", "key": f"port:{p}", "port": p, "proc": u.get("proc"), "owner": own,
                      "since": s["t"], "suggest": u["suggest"]})
    return sorted(items, key=lambda x: -x["since"]), handled


def mark_handled(key):
    with EXTRA_LOCK:
        seen = _read_json(SEEN_FILE, {})
        seen.setdefault("handled", {})[key] = time.time()
        _write_json(SEEN_FILE, seen)


# ─────────────────────────────────────────────
#  "인지함" 목록 (ack.json)
#  { "서비스이름": {"failed_at": 실패한 시각, "acked_at": 누른 시각} }
# ─────────────────────────────────────────────
def load_acks():
    try:
        return json.loads(ACK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_acks(acks):
    tmp = ACK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(acks, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ACK_FILE)       # 쓰다가 꺼져도 파일이 깨지지 않게 한 번에 바꿔치기


def apply_acks(failed):
    """실패 목록에 acked 표시를 붙여.
    실패한 시각이 인지할 때랑 다르면 = 정상으로 돌아왔다가 "새로" 실패한 거라 인지를 풀어."""
    with ACK_LOCK:
        acks, changed = load_acks(), False
        for f in failed:
            a = acks.get(f["unit"])
            if not a:
                continue
            if a.get("failed_at") == f.get("failed_at"):
                f["acked"], f["acked_at"] = True, a.get("acked_at")
            else:
                del acks[f["unit"]]
                changed = True
        if changed:
            save_acks(acks)


# 인지함을 누를 수 있는 숫자 경고 (APU 온도는 하드웨어 위험이라 일부러 뺌)
METRIC_ACK = {"mem_pct": ("메모리", "mem_pct"), "swap_pct": ("스왑", "swap_pct"), "disk_pct": ("디스크", "disk_pct")}
RANK = {"ok": 0, "warn": 1, "bad": 2}


def apply_metric_acks(hw, now):
    """숫자 경고에 인지 표시. 아래 경우엔 인지를 지워서 다시 경고가 뜨게 해:
    ① 정상으로 돌아옴  ② 더 나빠짐 (노랑 → 빨강)  ③ config 의 ack_metric_hours 시간이 지남"""
    hours = D.get("ack_metric_hours", 24) or 0
    out = {}
    with ACK_LOCK:
        acks, changed = load_acks(), False
        for k, (_, th) in METRIC_ACK.items():
            key = "metric:" + k
            a = acks.get(key)
            if not a:
                continue
            lv = level(hw.get(k), TH[th])
            expired = hours and now - a.get("acked_at", 0) > hours * 3600
            if lv == "ok" or RANK[lv] > RANK.get(a.get("level"), 0) or expired:
                del acks[key]
                changed = True
            else:
                out[k] = {**a, "until": a["acked_at"] + hours * 3600 if hours else None}
        if changed:
            save_acks(acks)
    return out


def apply_svc_port_acks(services, unknown):
    """서비스 줄 / 목록에 없는 포트 줄 인지. 아래 경우엔 인지를 지워 (다시 경고):
    서비스: 정상으로 돌아옴, 더 나빠짐(확인 필요 → 꺼짐)
    포트:   포트가 닫힘, 같은 포트를 다른 프로그램이 엶"""
    with ACK_LOCK:
        acks, changed = load_acks(), False
        by_name = {s["name"]: s for s in services}
        by_port = {u["port"]: u for u in unknown if u["exposure"] == "lan"}
        for key in [k for k in acks if k.startswith(("svc:", "port:"))]:
            a = acks[key]
            if key.startswith("svc:"):
                s = by_name.get(key[4:])
                if s and s["state"] in ("warn", "bad") and RANK[s["state"]] <= RANK.get(a.get("level"), 2):
                    s["acked"], s["acked_at"] = True, a.get("acked_at")
                    continue
            else:
                u = by_port.get(int(key[5:]) if key[5:].isdigit() else None)
                if u and (u.get("proc") or "?") == a.get("proc"):
                    u["acked"], u["acked_at"] = True, a.get("acked_at")
                    continue
            del acks[key]
            changed = True
        if changed:
            save_acks(acks)


# ─────────────────────────────────────────────
#  백그라운드 수집 루프
# ─────────────────────────────────────────────
STATE = {"snapshot": None}
HISTORY = deque(maxlen=int(D.get("history_hours", 6) * 3600 / D.get("sample_seconds", 60)))
_slow = {"urls": {}, "urls_at": 0, "hermes": None, "hermes_at": 0, "ports": {}, "ports_at": 0}
LOCK = threading.Lock()
WAKE = threading.Event()    # 인지함을 누르면 다음 수집을 기다리지 않고 바로 다시 모으게


def collect_once():
    now = time.time()
    hw = read_hardware()
    llama = {s["port"]: read_llama(s["port"]) for s in all_services()
             if s["name"].startswith("llama-server") and s.get("port")}

    if now - _slow["urls_at"] > 60:
        targets = [s["subdomain"] for s in all_services() if s.get("subdomain")]
        targets += [r["subdomain"] for r in CFG.get("remote", []) if r.get("subdomain")]
        urls = {t: check_url(f"https://{t}") for t in targets}
        urls.update({r["url"]: check_url(r["url"]) for r in CFG.get("remote", [])})
        _slow.update(urls=urls, urls_at=now)
    if now - _slow["hermes_at"] > 30:
        _slow.update(hermes=read_hermes(), hermes_at=now)

    # 포트는 자주 안 바뀌고, sudo 기록이 시스템 로그에 쌓이니까 30초마다만 확인
    if now - _slow["ports_at"] > 30:
        _slow.update(ports=read_ports(), ports_at=now)
    ports = _slow["ports"]
    services, unknown, others, failed = build_services(ports, _slow["urls"], llama)

    main_port = next((s.get("port") for s in (CFG.get("services") or []) if s["name"] == "llama-server"), None)
    hw["gen_tps"] = llama.get(main_port, {}).get("tps")

    # 상단 배지 (문제 있을 때만)
    badges, acked = [], []
    def add(lv, text, ack=None, id=None):
        # id: 알림이 "같은 문제"인지 알아보는 이름표 (text는 숫자가 계속 바뀌어서 못 씀)
        if lv in ("warn", "bad"):      # "off"(선택 서비스 꺼짐)는 경고 안 함
            badges.append({"level": lv, "text": text, "ack": ack, "id": id})
    add(level(hw["apu_temp"], TH["apu_temp_c"]), f"APU {hw['apu_temp']}°C", id="apu_temp")
    macks = apply_metric_acks(hw, now)
    for k, (label, th) in METRIC_ACK.items():
        lv, text = level(hw[k], TH[th]), f"{label} {hw[k]:.0f}%"
        if k in macks:                  # 인지한 숫자 경고는 경고에서 빼
            acked.append({"text": text, "level": lv, "ack": {"metric": k}, "tab": "overview", "id": "metric:" + k, **macks[k]})
        else:
            add(lv, text, {"metric": k}, id="metric:" + k)
    apply_svc_port_acks(services, unknown)
    for s in services:
        if s["state"] in ("warn", "bad"):
            text = f"{s['name']} {'꺼짐' if s['state'] == 'bad' else '확인 필요'}"
            if s.get("acked"):          # 인지한 서비스 → 경고에서 빼
                acked.append({"text": text, "level": s["state"], "ack": {"svc": s["name"]}, "tab": "services",
                              "acked_at": s.get("acked_at"), "id": "svc:" + s["name"]})
            else:
                add(s["state"], text, {"svc": s["name"]}, id="svc:" + s["name"])
    apply_acks(failed)
    for f in failed:
        if f.get("acked"):              # 인지함을 누른 실패는 경고에서 빼
            acked.append({"text": f"{f['unit'].removesuffix('.service')} 실패", "level": "bad",
                          "ack": {"unit": f["unit"]}, "tab": "services", "acked_at": f.get("acked_at"), "id": "failed:" + f["unit"]})
        else:
            add("bad", f"{f['unit']} 실패", {"unit": f["unit"]}, id="failed:" + f["unit"])
    # 내부 전용(127.0.0.1) 포트는 밖에서 못 들어오니까 표에만 보여주고 경고는 안 띄워
    new_items, new_handled = detect_new(unknown, others, now)
    new_ports = {it["port"] for it in new_items if it.get("port")}
    for it in new_items:                # 🆕 새로 생긴 것 → 노랑 경고 (알림은 노랑 기준: 10분 뒤, 조용한 시간엔 요약)
        label = f"🆕 새 서비스 {it['unit'].removesuffix('.service')}" + (f" (포트 {it['port']})" if it.get("port") else "") \
            if it["kind"] == "svc" else f"🆕 새 포트 {it['port']} ({it.get('proc') or '?'})"
        add("warn", label, id="new:" + it["key"])
    unknown_lan = [u for u in unknown if u["exposure"] == "lan" and not u.get("acked") and u["port"] not in new_ports]
    for u in unknown:
        if u.get("acked"):
            acked.append({"text": f"포트 {u['port']} ({u.get('proc') or '?'})", "level": "warn", "ack": {"port": u["port"]},
                          "tab": "services", "acked_at": u.get("acked_at"), "id": f"port:{u['port']}"})
    if unknown_lan:
        add("warn", f"미확인 포트 {len(unknown_lan)}개", id="ports:unknown")
    hz = _slow["hermes"] or {}
    for p in hz.get("platforms", []):
        add(p["state"], f"{p['name']} {'끊김' if p['state'] == 'bad' else '불안정'}", id="hermes:" + p["name"])
    for bot in hz.get("bots", []):
        if bot["parked"]:
            add("bad", f"{bot['name']} 멈춤(parked)", id=f"bot:{bot['id']}:parked")
        for p in bot["platforms"]:
            add(p["level"], f"{bot['name']} {p['name']} {p['state']}", id=f"bot:{bot['id']}:{p['name']}")
    gw = hz.get("gateway")
    if gw and gw.get("heartbeat") and now - gw["heartbeat"] > 180:
        add("bad", "hermes 게이트웨이 응답 없음", id="hermes:gateway")
    for m in hz.get("mcp", []):
        add(m["level"], f"MCP {m['name']} {m['state']}", id="mcp:" + m["name"])

    # 웹훅 알림: 오래 계속된 문제만 보냄 (인지한 건 빼고)
    try:
        # 추가했거나 넘긴 "새로 생긴 것"은 인지처럼 "해결" 알림 없이 조용히 끝
        NOTIFY.process(badges, {a["id"] for a in acked} | {"new:" + k for k in new_handled}, now)
    except Exception as e:
        print("notify process error:", repr(e), flush=True)

    overall = "bad" if any(b["level"] == "bad" for b in badges) else ("warn" if badges else "ok")
    snap = {
        "server_time": now, "boot_time": psutil.boot_time(), "hostname": socket.gethostname(), "ip": primary_ip(),
        "agent_enabled": bool((CFG.get("hermes") or {}).get("unit")),
        "overall": overall, "badges": badges, "hw": hw, "llama": llama,
        "services": services, "unknown_ports": unknown, "other_services": others,
        "failed": failed, "new_items": new_items, "acked": acked, "acked_count": len(acked), "metric_acks": macks, "hermes": hz,
        "notify": NOTIFY.status(now), "thresholds": TH, "title": TITLE,
    }
    with LOCK:
        STATE["snapshot"] = snap
        if not HISTORY or now - HISTORY[-1]["t"] >= D.get("sample_seconds", 60) - 1:
            HISTORY.append({"t": now, **{k: hw[k] for k in (
                "cpu_pct", "gpu_pct", "apu_temp", "mem_pct", "swap_pct", "apu_power", "gen_tps")}})


def loop():
    psutil.cpu_percent(interval=None)  # 첫 호출은 0이 나와서 한 번 버리기
    while True:
        try:
            collect_once()
        except Exception as e:  # 수집 중 에러가 나도 대시보드는 계속 살아 있게
            print("collect error:", repr(e), flush=True)
        WAKE.wait(D.get("refresh_seconds", 5))
        WAKE.clear()


# ─────────────────────────────────────────────
#  Flask
# ─────────────────────────────────────────────
app = Flask(__name__)
# 서비스 제어 모듈 (관리 버튼 → start/stop/restart/enable/disable)
app.register_blueprint(make_service_control(all_services, CFG))


@app.get("/")
def index():
    # 앱 열기 링크: 회사 PC(회사 공인 IP에서 접속한 PC 브라우저)는 내부 주소로 등록된 Mattermost 앱을 써서 따로 구분
    op = (CFG.get("hermes") or {}).get("open") or {}
    ip = request.headers.get("CF-Connecting-IP") or request.remote_addr or ""
    open_cfg = {"team": op.get("team"), "server": op.get("server"), "server_office": op.get("server_office"),
                "at_office": bool(op.get("office_ip")) and ip == op.get("office_ip")}
    return render_template("index.html", title=TITLE, refresh=D.get("refresh_seconds", 5), open_cfg=open_cfg,
                           version=D.get("version", ""), author=D.get("author", ""), updated=D.get("updated", ""))


@app.get("/manifest.webmanifest")
def manifest():
    """앱 설치(PWA)용 설명서. 이름을 서버 제목(hostname)으로 만들어서 어느 서버에서나 자기 이름으로 나옴"""
    short = TITLE.split()[0]
    r = jsonify({
        "name": TITLE, "short_name": short[:12], "description": f"{TITLE} 상태 대시보드",
        "start_url": "./", "scope": "./", "display": "standalone", "lang": "ko",
        "background_color": "#0d0e10", "theme_color": "#0d0e10",
        "icons": [{"src": f"static/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": p}
                  for n in (192, 512) for p in ("any", "maskable")],
    })
    r.mimetype = "application/manifest+json"
    return r


@app.get("/sw.js")
def service_worker():
    """앱 설치용 서비스 워커. 맨 위 주소(/)에 있어야 화면 전체를 담당할 수 있어."""
    r = send_from_directory(BASE / "static", "sw.js", mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"     # 고치면 바로 반영되게
    return r


@app.get("/api/status")
def api_status():
    with LOCK:
        return jsonify(STATE["snapshot"] or {"loading": True})


@app.get("/api/history")
def api_history():
    with LOCK:
        return jsonify(list(HISTORY))


@app.route("/api/ack", methods=["POST", "DELETE"])
def api_ack():
    # JSON 요청만 받아 (다른 사이트에서 몰래 보내는 폼 요청 막기)
    if not request.is_json:
        return jsonify({"ok": False, "error": "json only"}), 415
    body = request.get_json(silent=True) or {}
    unit, metric = str(body.get("unit", "")), str(body.get("metric", ""))
    svc, port = str(body.get("svc", "")), str(body.get("port", ""))
    with LOCK:
        snap = STATE["snapshot"] or {}
    with ACK_LOCK:
        acks = load_acks()
        if svc:
            cur = next((s for s in snap.get("services") or [] if s["name"] == svc), None)
            if request.method == "POST":
                if not cur or cur["state"] not in ("warn", "bad"):
                    return jsonify({"ok": False, "error": "지금 문제 있는 서비스가 아니야"}), 404
                acks["svc:" + svc] = {"level": cur["state"], "acked_at": time.time()}
            else:
                acks.pop("svc:" + svc, None)
        elif port:
            cur = next((u for u in snap.get("unknown_ports") or [] if str(u["port"]) == port), None)
            if request.method == "POST":
                if not cur:
                    return jsonify({"ok": False, "error": "지금 열려 있는 미확인 포트가 아니야"}), 404
                acks["port:" + port] = {"proc": cur.get("proc") or "?", "acked_at": time.time()}
            else:
                acks.pop("port:" + port, None)
        elif metric:
            if metric not in METRIC_ACK:
                return jsonify({"ok": False, "error": "인지할 수 없는 항목이야"}), 400
            key = "metric:" + metric
            if request.method == "POST":
                lv = level((snap.get("hw") or {}).get(metric), TH[METRIC_ACK[metric][1]])
                if lv == "ok":
                    return jsonify({"ok": False, "error": "지금 정상이라 인지할 게 없어"}), 404
                acks[key] = {"level": lv, "acked_at": time.time()}
            else:
                acks.pop(key, None)
        else:
            cur = next((f for f in snap.get("failed") or [] if f["unit"] == unit), None)
            if request.method == "POST":
                if not cur:
                    return jsonify({"ok": False, "error": "지금 실패 상태인 서비스가 아니야"}), 404
                acks[unit] = {"failed_at": cur.get("failed_at"), "acked_at": time.time()}
            else:
                acks.pop(unit, None)
        save_acks(acks)
    WAKE.set()
    return jsonify({"ok": True})


@app.post("/api/services/add")
def api_services_add():
    """[목록에 추가] → services_extra.json 에 저장. 바로 서비스 표에 올라감 (재시작 X)"""
    if not request.is_json:
        return jsonify({"ok": False, "error": "json only"}), 415
    b = request.get_json(silent=True) or {}
    name = str(b.get("name") or "").strip()
    scope = str(b.get("scope") or "")
    unit = str(b.get("unit") or "").strip() or None
    port = b.get("port")
    if not name or len(name) > 60:
        return jsonify({"ok": False, "error": "이름을 넣어 줘"}), 400
    if scope not in ("system", "user", "manual"):
        return jsonify({"ok": False, "error": "scope 가 이상해"}), 400
    if scope != "manual" and not unit:
        return jsonify({"ok": False, "error": "서비스(unit) 이름이 없어"}), 400
    try:
        port = int(port) if port not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "포트 번호가 이상해"}), 400
    if scope == "manual" and not port:
        return jsonify({"ok": False, "error": "직접 띄운 프로그램은 포트가 있어야 켜졌는지 알 수 있어"}), 400
    with EXTRA_LOCK:
        if any(s["name"] == name for s in all_services()):
            return jsonify({"ok": False, "error": f"'{name}' 은(는) 이미 목록에 있어"}), 400
        entry = {"name": name, "scope": scope, "added": time.time()}
        if unit:
            entry["unit"] = unit
        if port:
            entry["port"] = port
        if str(b.get("note") or "").strip():
            entry["note"] = str(b["note"]).strip()[:120]
        if b.get("optional"):
            entry["optional"] = True
        if b.get("process"):
            entry["process"] = str(b["process"])[:40]
        _write_json(EXTRA_FILE, load_extras() + [entry])
    if b.get("key"):
        mark_handled(str(b["key"]))
    if port:
        _slow["ports_at"] = 0           # 포트 상태 바로 다시 읽기
    WAKE.set()
    return jsonify({"ok": True})


@app.post("/api/services/remove")
def api_services_remove():
    """화면에서 추가한 서비스만 뺄 수 있어 (config.yaml 에 적은 건 X)"""
    if not request.is_json:
        return jsonify({"ok": False, "error": "json only"}), 415
    name = str((request.get_json(silent=True) or {}).get("name") or "")
    with EXTRA_LOCK:
        ex = load_extras()
        if not any(s["name"] == name for s in ex):
            return jsonify({"ok": False, "error": "화면에서 추가한 서비스만 뺄 수 있어 (config.yaml 것은 직접 고쳐 줘)"}), 400
        _write_json(EXTRA_FILE, [s for s in ex if s["name"] != name])
    WAKE.set()
    return jsonify({"ok": True})


@app.post("/api/services/skip")
def api_services_skip():
    """🆕 새로 생긴 것 [넘기기]: 알고 있는 거니까 목록엔 안 올리고 표시만 끔"""
    if not request.is_json:
        return jsonify({"ok": False, "error": "json only"}), 415
    key = str((request.get_json(silent=True) or {}).get("key") or "")
    if not key.startswith(("svc:", "port:")):
        return jsonify({"ok": False, "error": "이름표가 이상해"}), 400
    mark_handled(key)
    WAKE.set()
    return jsonify({"ok": True})


@app.post("/api/notify/test")
def api_notify_test():
    """[테스트 알림 보내기] 버튼. secrets.yaml 을 새로 읽어서 바로 보내 봄"""
    if not request.is_json:
        return jsonify({"ok": False, "error": "json only"}), 415
    if not NOTIFY.channels():
        return jsonify({"ok": False, "error": "secrets.yaml 에 받을 곳(웹훅 주소/봇 토큰)이 아직 없어"}), 400
    entry = NOTIFY.test()
    WAKE.set()
    return jsonify({"ok": entry["ok"], "results": entry["results"]})


@app.post("/api/notify/quiet")
def api_notify_quiet():
    """조용한 시간 토글·시간·요일을 화면에서 바꿀 때. 바로 적용돼 (재시작 X)"""
    if not request.is_json:
        return jsonify({"ok": False, "error": "json only"}), 415
    try:
        q = NOTIFY.set_quiet(request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    WAKE.set()
    return jsonify({"ok": True, "quiet": q})


if __name__ == "__main__":
    if DEMO:
        import demo
        demo.install(globals())
    threading.Thread(target=loop, daemon=True).start()
    try:
        from waitress import serve
        print(f"대시보드 시작: http://{D.get('host', '127.0.0.1')}:{D.get('port', 5070)}", flush=True)
        serve(app, host=D.get("host", "127.0.0.1"), port=D.get("port", 5070), threads=4)
    except ImportError:
        app.run(host=D.get("host", "127.0.0.1"), port=D.get("port", 5070), threaded=True)
