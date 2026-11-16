"""BC250_DEMO=1 일 때 쓰는 가짜 데이터 (화면 확인용). 실제 서버에서는 안 쓰여."""
import math
import random
import time


def install(g):
    t0 = time.time()

    def busy(t):
        return (int(t / 300) % 7) in (2, 3) or (int(t / 300) % 11) == 5

    def hw(t=None):
        t = t or time.time()
        b = busy(t)
        r = random.random
        return {
            "cpu_pct": round((38 if b else 7) + r() * 10, 1),
            "gpu_pct": round((90 if b else 3) + r() * 8, 1),
            "apu_temp": round((76 if b else 61) + r() * 5 + (9 if int(t / 300) % 23 == 4 else 0), 1),
            "gpu_temp": round((70 if b else 57) + r() * 3, 1),
            "apu_power": round((120 if b else 33) + r() * 10, 1),
            "pump_rpm": 968 + int(r() * 60),
            "mem_used": 12.02, "mem_total": 14.85, "mem_pct": 81.0,
            "swap_used": 3.62, "swap_total": 4.0, "swap_pct": 90.5,
            "disk_used": 56.7, "disk_total": 111.3, "disk_pct": 51.0,
            "vram_used": 7.4, "vram_total": 8.0, "gtt_used": 3.1, "gtt_total": 7.4,
        }

    def ports():
        P = lambda a, e, p, pid: {"addr": a, "exposure": e, "proc": p, "pid": pid}
        return {
            8080: P("0.0.0.0", "lan", "llama-server", 191456), 8081: P("0.0.0.0", "lan", "llama-server", 109490),
            53: P("127.0.0.53", "internal", "systemd-resolve", 545), 20241: P("127.0.0.1", "internal", "cloudflared", 936),
            11987: P("127.0.0.1", "internal", "coolercontrold", 702), 5355: P("0.0.0.0", "lan", "systemd-resolve", 545),
            5050: P("127.0.0.1", "internal", "python", 112600), 56301: P("127.0.0.1", "internal", "hermes", 281486),
            9119: P("0.0.0.0", "lan", "hermes", 265264), 22: P("0.0.0.0", "lan", "sshd", 708),
            8090: P("*", "lan", "pocketbase", 64885), 5001: P("*", "lan", "ds2api", 121373),
            6001: P("*", "lan", "MainThread", 153660), 3000: P("*", "lan", "node-MainThread", 1140),
            5070: P("127.0.0.1", "internal", "python", 4242),
        }

    def show(units, user):
        now = time.time()
        return {u: {"active": "active", "sub": "running", "since": int(now - 86400 * (3 + i % 9) - i * 3333),
                    "changed": int(t0 - 7200 - i * 600),
                    "restarts": 1 if "gateway" in u else 0, "pid": 1000 + i} for i, u in enumerate(units)}

    def lst(user, state):
        if state == "failed":
            return [] if user else [{"unit": "bc250-cu-live-manager.service", "desc": "BC250 CU live manager", "user": False},
                                    {"unit": "grub-btrfs-snapper.service", "desc": "grub-btrfs snapper", "user": False}]
        names = ["ananicy-cpp", "avahi-daemon", "bluetooth", "cyan-skillfish-governor-smu", "dbus-broker",
                 "getty@tty1", "NetworkManager", "polkit", "systemd-journald", "systemd-logind",
                 "systemd-oomd", "systemd-resolved", "systemd-timesyncd", "systemd-udevd", "upower"]
        return [{"unit": n + ".service", "desc": ""} for n in (names if not user else ["dbus-broker"])]

    def hermes():
        now = time.time()
        return {
            "window_h": 6,
            "platforms": [{"name": "Mattermost", "state": "ok", "fails_1h": 0, "last": now - 120},
                          {"name": "Telegram", "state": "warn", "fails_1h": 6, "last": now - 300}],
            "gateway": {"state": "running", "heartbeat": now - 20, "active_agents": 0, "version": "0.22.5"},
            "bots": [
                {"id": "thia", "mm_user": "thia", "name": "Thia", "role": "비서", "parked": False, "level": "ok", "model": "gemini-3.8-flash",
                 "last": now - 180, "today": 23, "platforms": [{"name": "Mattermost", "state": "connected", "level": "ok"}]},
                {"id": "tessa", "mm_user": "tessa", "name": "Tessa", "role": "시스템엔지니어", "parked": False, "level": "ok", "model": "gemma-4-12B-it-qat-UD-Q4_K_XL",
                 "last": now - 4000, "today": 4, "platforms": [{"name": "Mattermost", "state": "connected", "level": "ok"}]},
                {"id": "dek", "mm_user": "dek", "name": "Dek", "role": "보안엔지니어", "parked": False, "level": "ok", "model": "deepseek/deepseek-v4.1-flash",
                 "last": now - 90000, "today": 0, "platforms": [{"name": "Mattermost", "state": "connected", "level": "ok"}]},
                {"id": "default", "tg_user": "demo_bot", "name": "기본", "role": "Telegram", "parked": False, "level": "ok", "model": "gemini-3.8-flash",
                 "last": now - 7200, "today": 2, "platforms": [{"name": "Telegram", "state": "connected", "level": "ok"}]}],
            "mcp": [{"name": "mypc", "state": "parked", "at": now - 6000, "level": "bad"}],
            "groups": [{"label": "Telegram 연결 실패", "count": 14, "last": now - 300},
                       {"label": "Nous 인증 없음", "count": 8, "last": now - 11000},
                       {"label": "도구 실행 오류", "count": 2, "last": now - 11000},
                       {"label": "MCP 연결 끊김", "count": 2, "last": now - 6000}],
            "tail": [{"t": now - 300 + i * 10, "level": "WARNING",
                      "msg": "plugins.platforms.telegram.telegram_network: [Telegram] Sticky Telegram path 149.154.166.110 failed; re-walking IPv4 literals before the hostname"}
                     for i in range(8)] + [{"t": now - 60, "level": "WARNING",
                      "msg": "tools.mcp_tool: MCP server 'mypc' failed after 5 reconnection attempts, parking; will self-probe every 300s (state: degraded → parked)"}],
        }

    def url(u):
        bad = "ds." in u
        return {"ok": not bad, "code": 502 if bad else (302 if "pb." in u or "mon." in u else 200)}

    g.update(read_hardware=hw, read_ports=ports, systemd_show=show, systemd_list=lst,
             read_hermes=hermes, check_url=url,
             read_llama=lambda port: {"healthy": True, "busy": False, "tps": 14.2 if busy(time.time()) else 0.0})
    g["psutil"].boot_time = lambda: t0 - (13 * 86400 + 10 * 3600 + 15 * 60)

    # 알림: 데모 기록은 따로 저장하고, 진짜 Mattermost/Telegram 으로는 안 보내 (BC250_DEMO_NOTIFY=1 이면 보냄)
    import os
    n = g["NOTIFY"]
    n.state_file = g["BASE"] / "notify_state.demo.json"
    n.state = n._load()
    if os.environ.get("BC250_DEMO_NOTIFY") != "1":
        n.secrets = lambda: {}

    # 6시간치 기록 미리 채우기
    step = g["D"]["sample_seconds"]
    for i in range(int(6 * 3600 / step), 0, -1):
        t = t0 - i * step
        h = hw(t)
        g["HISTORY"].append({"t": t, "cpu_pct": h["cpu_pct"], "gpu_pct": h["gpu_pct"], "apu_temp": h["apu_temp"],
                             "mem_pct": 80 + math.sin(i / 40), "swap_pct": 88 + math.sin(i / 60) * 2,
                             "apu_power": h["apu_power"], "gen_tps": 14.0 + random.random() if busy(t) else 0})
