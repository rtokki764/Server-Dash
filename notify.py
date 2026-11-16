"""
웹훅 알림 (Mattermost / Telegram)

흐름
  1) 대시보드가 5초마다 상단 배지(경고 목록)를 process() 에 넘겨줘
  2) 배지마다 "언제부터 문제였는지" 기억해 뒀다가
       빨강이 bad_seconds(기본 1분) 넘게 계속되면 → 전송
       노랑이 warn_seconds(기본 10분) 넘게 계속되면 → 전송
  3) 보낸 문제가 resolve_seconds(기본 1분) 동안 안 보이면 → "✅ 해결" 전송
  4) [인지함] 누른 건 안 보내고, 보낸 뒤에 인지하면 "해결" 없이 조용히 끝내
  5) 같은 문제는 cooldown_minutes(기본 30분) 안에 다시 안 보내 (껐다 켜졌다 반복할 때 도배 방지)
  6) 이 기억은 notify_state.json 에 저장 → 대시보드를 재시작해도 같은 알림이 또 안 가
  7) 조용한 시간(quiet_hours, 기본 00:00~08:00)에는
       빨강은 그대로 바로 보내고, 노랑은 안 보내고 모아 뒀다가
       조용한 시간이 끝나면 "🌙 밤사이 요약" 한 통으로 보내 (아무 일 없었으면 안 보냄)

받을 곳(웹훅 주소·토큰)은 secrets.yaml 에 따로 적어. 보낼 때마다 새로 읽으니까
secrets.yaml 을 고친 뒤엔 재시작 없이 [테스트 알림 보내기]로 바로 확인할 수 있어.
"""
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

try:
    from datetime import datetime
    from zoneinfo import ZoneInfo
except Exception:       # 아주 옛날 파이썬이면 서버 시간대로
    ZoneInfo = None

RANK = {"ok": 0, "warn": 1, "bad": 2}
ICON = {"warn": "🟡", "bad": "🔴"}


def _dur(sec):
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}초"
    m = sec // 60
    if m < 60:
        return f"{m}분"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}시간 {m}분" if m else f"{h}시간"
    d, h = divmod(h, 24)
    return f"{d}일 {h}시간"


def _post_json(url, data, timeout=8):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "bc250-dash"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(2000).decode("utf-8", "replace")


class Notifier:
    def __init__(self, base, cfg, title="Server"):
        self.base = Path(base)
        self.cfg = cfg or {}
        self.title = title
        self.state_file = self.base / "notify_state.json"
        self.secrets_file = self.base / "secrets.yaml"
        self.settings_file = self.base / "notify_settings.json"   # 화면에서 바꾼 조용한 시간 (zip 설치해도 안 지워짐)
        self.lock = threading.Lock()
        self.q = queue.Queue()
        self.state = self._load()
        threading.Thread(target=self._worker, daemon=True).start()

    # ── 설정값 ──
    # 우선순위: 화면에서 저장한 값(notify_settings.json) > config.yaml 의 notify > 기본값
    EDITABLE = ("enabled", "bad_seconds", "warn_seconds", "resolve_seconds", "cooldown_minutes",
                "send_resolved", "link", "mute")

    def _saved(self):
        try:
            return json.loads(self.settings_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _c(self, key, default):
        sv = self._saved().get("notify") or {}
        v = sv[key] if key in sv else self.cfg.get(key)
        return default if v is None else v

    @property
    def enabled(self):
        return bool(self._c("enabled", True))

    def rules(self):
        return {"bad_seconds": self._c("bad_seconds", 60), "warn_seconds": self._c("warn_seconds", 600),
                "resolve_seconds": self._c("resolve_seconds", 60), "cooldown_minutes": self._c("cooldown_minutes", 30),
                "send_resolved": bool(self._c("send_resolved", True))}

    # ── 조용한 시간 ──
    def quiet_settings(self):
        """조용한 시간 설정 전체. config.yaml 의 quiet_hours 가 기본값이고,
        화면에서 저장하면 notify_settings.json 값이 우선이야."""
        q = self.cfg.get("quiet_hours") or {}
        s = {"enabled": bool(q.get("start") and q.get("end")), "start": str(q.get("start") or "00:00"),
             "end": str(q.get("end") or "08:00"), "days": [0, 1, 2, 3, 4, 5, 6],
             "weekend_all_day": False, "tz": q.get("tz") or ""}   # tz 가 비면 서버 시간대
        try:
            saved = json.loads(self.settings_file.read_text(encoding="utf-8")).get("quiet") or {}
            s.update({k: v for k, v in saved.items() if k in s})
        except Exception:
            pass
        return s

    def quiet(self):
        """켜져 있으면 설정, 꺼져 있으면 None"""
        s = self.quiet_settings()
        return s if s["enabled"] else None

    def set_quiet(self, data):
        """화면에서 바꾼 값 검사하고 저장. 틀리면 ValueError"""
        import re
        cur = self.quiet_settings()
        for k in ("start", "end"):
            if k in data:
                v = str(data[k])
                if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
                    raise ValueError(f"시간 형식이 이상해: {v} (예: 07:30)")
                cur[k] = v
        if "days" in data:
            days = sorted({int(d) for d in data["days"] if str(d).isdigit() and 0 <= int(d) <= 6})
            cur["days"] = days
        for k in ("enabled", "weekend_all_day"):
            if k in data:
                cur[k] = bool(data[k])
        if "tz" in data:
            tz = str(data["tz"]).strip()
            if tz and ZoneInfo:
                try:
                    ZoneInfo(tz)
                except Exception:
                    raise ValueError(f"시간대 이름이 이상해: {tz} (예: Asia/Seoul, 비우면 서버 시간대)")
            cur["tz"] = tz
        saved = {k: cur[k] for k in ("enabled", "start", "end", "days", "weekend_all_day", "tz")}
        self._write_saved("quiet", saved)
        names = "월화수목금토일"
        dtext = "매일" if len(cur["days"]) == 7 else ("요일 없음" if not cur["days"] else "".join(names[d] for d in cur["days"]))
        text = (f"🌙 조용한 시간 {'켬' if cur['enabled'] else '끔'} · {cur['start']}~{cur['end']} · {dtext}"
                + (" · 주말 종일" if cur["weekend_all_day"] else ""))
        with self.lock:
            self.state["log"] = (self.state["log"] + [{"t": time.time(), "kind": "setting", "text": text,
                                                        "results": {"설정": "ok"}, "ok": True}])[-10:]
            self._save()
        return cur

    def _write_saved(self, section, value):
        """notify_settings.json 의 한 부분만 바꿔 저장 (다른 부분은 그대로)"""
        data = self._saved()
        data[section] = value
        tmp = self.settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.settings_file)

    def set_settings(self, data):
        """화면에서 바꾼 알림 규칙 검사하고 저장. 틀리면 ValueError. 바로 적용돼 (재시작 X)"""
        cur = {k: self._c(k, None) for k in self.EDITABLE}
        sv = dict(self._saved().get("notify") or {})
        ranges = {"bad_seconds": (0, 86400), "warn_seconds": (0, 86400), "resolve_seconds": (10, 3600),
                  "cooldown_minutes": (0, 10080)}
        names = {"bad_seconds": "빨강 대기", "warn_seconds": "노랑 대기", "resolve_seconds": "해결 확인",
                 "cooldown_minutes": "쿨다운"}
        for k, (lo, hi) in ranges.items():
            if k in data:
                try:
                    v = int(float(data[k]))
                except (TypeError, ValueError):
                    raise ValueError(f"{names[k]} 값이 숫자가 아니야")
                if not lo <= v <= hi:
                    raise ValueError(f"{names[k]} 값은 {lo}~{hi} 사이여야 해")
                sv[k] = v
        for k in ("enabled", "send_resolved"):
            if k in data:
                sv[k] = bool(data[k])
        if "link" in data:
            link = str(data["link"]).strip()
            if link and not link.startswith(("http://", "https://")):
                raise ValueError("대시보드 주소는 http:// 또는 https:// 로 시작해야 해")
            sv["link"] = link[:200]
        if "mute" in data:
            items = [str(m).strip() for m in (data["mute"] or []) if str(m).strip()]
            sv["mute"] = sorted(set(items))[:200]
        self._write_saved("notify", sv)
        with self.lock:
            self.state["log"] = (self.state["log"] + [{"t": time.time(), "kind": "setting", "text": "🔔 알림 규칙을 바꿨어",
                                                        "results": {"설정": "ok"}, "ok": True}])[-10:]
            self._save()
        return {k: self._c(k, None) for k in self.EDITABLE}

    def _local(self, now, tz):
        if ZoneInfo and tz:
            try:
                return datetime.fromtimestamp(now, ZoneInfo(tz))
            except Exception:
                pass
        return datetime.fromtimestamp(now)

    def _quiet_at(self, now, q):
        """그 시각이 조용한 시간인지 (요일·주말 종일·자정 넘는 범위까지)"""
        d = self._local(now, q["tz"])
        wd, cur = d.weekday(), d.hour * 60 + d.minute       # 월=0 … 일=6
        if q.get("weekend_all_day") and wd >= 5:
            return True
        hm = lambda s: int(s.split(":")[0]) * 60 + int(s.split(":")[1])
        st, en, days = hm(q["start"]), hm(q["end"]), set(q.get("days") or [])
        if st == en:                                          # 같은 시각이면 그 요일 하루 종일
            return wd in days
        if st < en:
            return st <= cur < en and wd in days
        if cur >= st:                                         # 밤 부분 (예: 23:00~) → 오늘 요일
            return wd in days
        return cur < en and (wd - 1) % 7 in days              # 새벽 부분 (~07:00) → 어제 시작한 것

    def in_quiet(self, now):
        """(지금 조용한 시간인지, 끝날 때까지 남은 초)"""
        q = self.quiet()
        if not q or not self._quiet_at(now, q):
            return False, None
        base = now - now % 60
        for i in range(1, 60 * 24 * 8 + 1):                  # 1분씩 앞으로 가면서 끝나는 시각 찾기 (최대 8일)
            if not self._quiet_at(base + i * 60, q):
                return True, base + i * 60 - now
        return True, None

    def _muted(self, bid):
        return any(bid == m or (m.endswith("*") and bid.startswith(m[:-1])) for m in (self._c("mute", None) or []))

    # ── 저장 ──
    def _load(self):
        try:
            s = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            s = {}
        s.setdefault("active", {})      # 지금 문제인 것들 {id: {...}}
        s.setdefault("last_sent", {})   # id별 마지막 전송 시각 (쿨다운용)
        s.setdefault("log", [])         # 최근 전송 기록
        s.setdefault("held", {})        # 조용한 시간에 모아 둔 노랑 {id: {text, since, end}}
        return s

    def _save(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    def secrets(self):
        try:
            return yaml.safe_load(self.secrets_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def channels(self):
        """설정이 채워진 받을 곳만 {이름: 설정}"""
        s, out = self.secrets(), {}
        mm = s.get("mattermost") or {}
        if str(mm.get("webhook_url") or "").startswith("http"):
            out["mattermost"] = mm
        tg = s.get("telegram") or {}
        if tg.get("bot_token") and tg.get("chat_id"):
            out["telegram"] = tg
        return out

    # ── 핵심: 배지 목록을 보고 보낼 것 정하기 ──
    def process(self, badges, acked_ids, now=None):
        if not self.enabled:
            return
        now = now or time.time()
        R = self.rules()
        cooldown = R["cooldown_minutes"] * 60
        cur = {b["id"]: b for b in badges if b.get("id") and not self._muted(b["id"])}
        alerts, resolved, changed = [], [], False
        quiet, _ = self.in_quiet(now)
        digest = None
        with self.lock:
            act, last, held = self.state["active"], self.state["last_sent"], self.state["held"]
            # 조용한 시간이 끝났는데 모아 둔 게 있으면 → 아침 요약 만들기
            if not quiet and held:
                digest = self._digest(held, act, now)
                for bid in held:
                    a = act.get(bid)
                    if a and a.get("held"):                     # 아직 계속 중 → 요약으로 보낸 걸로 처리
                        a.pop("held", None)
                        a["sent_level"], a["sent_at"] = "warn", now
                        last[bid] = now
                held.clear()
                changed = True
            for bid, b in cur.items():
                lv = b["level"]
                a = act.get(bid)
                if a is None:
                    a = act[bid] = {"text": b["text"], "level": lv, "first": now,
                                    "bad_since": now if lv == "bad" else None, "sent_level": None}
                    changed = True
                if a.pop("gone_since", None) is not None:
                    changed = True
                a["text"] = b["text"]
                if lv == "bad" and a.get("bad_since") is None:
                    a["bad_since"] = now
                elif lv != "bad":
                    a["bad_since"] = None
                a["level"] = lv
                due = (lv == "bad" and now - a["bad_since"] >= R["bad_seconds"]) or now - a["first"] >= R["warn_seconds"]
                if not due or RANK[lv] <= RANK.get(a.get("sent_level"), 0):
                    continue
                if quiet and lv == "warn":                      # 조용한 시간의 노랑 → 모아 두기
                    h = held.setdefault(bid, {"since": a["first"], "end": None})
                    if h.get("text") != b["text"] or not a.get("held"):
                        h["text"], a["held"] = b["text"], True
                        changed = True
                    continue
                if a.pop("held", None):                         # 모아 둔 노랑이 빨강이 됨 → 바로 보내고 요약에선 빼
                    held.pop(bid, None)
                worse = a.get("sent_level") is not None        # 노랑 → 빨강으로 나빠짐: 쿨다운 무시
                if not worse and now - last.get(bid, 0) < cooldown:
                    continue                                     # 얼마 전에 보낸 문제 → 쿨다운 끝나면 보냄
                alerts.append({"id": bid, "text": b["text"], "level": lv, "since": a["first"], "worse": worse})
                a["sent_level"], a["sent_at"] = lv, now
                last[bid] = now
                changed = True

            for bid in list(act):
                if bid in cur:
                    continue
                a = act[bid]
                if bid in acked_ids or self._muted(bid):        # 인지함/음소거 → 조용히 끝
                    held.pop(bid, None)
                    del act[bid]
                    changed = True
                    continue
                if a.get("gone_since") is None:
                    a["gone_since"] = now
                    changed = True
                if now - a["gone_since"] >= R["resolve_seconds"]:
                    if a.get("held") and bid in held:          # 밤에 모아 둔 노랑이 해결됨 → 요약에 "해결됨"으로
                        held[bid]["end"] = a["gone_since"]
                    elif a.get("sent_level") and R["send_resolved"]:
                        resolved.append({"id": bid, "text": a["text"], "dur": a["gone_since"] - a["first"]})
                    del act[bid]
                    changed = True
            if changed:
                self._save()
        if digest:
            self.q.put(digest)
        if alerts or resolved:
            self.q.put(self._format(alerts, resolved, now))

    def _digest(self, held, act, now):
        q = self.quiet() or {"start": "?", "end": "?", "tz": ""}
        hm = lambda t: (datetime.fromtimestamp(t, ZoneInfo(q["tz"])) if ZoneInfo and q["tz"] else datetime.fromtimestamp(t)).strftime("%H:%M")
        lines = [f"🌙 {self.title} 조용한 시간 요약 · 노랑 {len(held)}건"]
        for bid, h in sorted(held.items(), key=lambda x: x[1].get("since", 0)):
            a = act.get(bid)
            if h.get("end") is None and a and a.get("held"):
                lines.append(f"🟡 {h.get('text', bid)} — {hm(h['since'])}부터, 아직 계속")
            else:
                end = h.get("end") or now
                lines.append(f"✅ {h.get('text', bid)} — {hm(h['since'])}~{hm(end)} (해결됨)")
        link = self._c("link", None)
        if link:
            lines.append(link)
        return {"text": "\n".join(lines), "kind": "digest", "count": len(held)}

    def _format(self, alerts, resolved, now):
        lines = []
        if alerts:
            top = "bad" if any(a["level"] == "bad" for a in alerts) else "warn"
            lines.append(f"{ICON[top]} {self.title} 경고 {len(alerts)}건")
            for a in sorted(alerts, key=lambda x: -RANK[x["level"]]):
                tail = "노랑 → 빨강으로 나빠짐" if a["worse"] else f"{_dur(now - a['since'])}째 계속"
                lines.append(f"{ICON[a['level']]} {a['text']} — {tail}")
        if resolved:
            if lines:
                lines.append("")
            lines.append(f"✅ {self.title} 해결 {len(resolved)}건")
            for r in resolved:
                lines.append(f"✅ {r['text']} — {_dur(r['dur'])} 만에 해결")
        link = self._c("link", None)
        if link:
            lines.append(link)
        return {"text": "\n".join(lines), "kind": "alert" if alerts else "resolved",
                "count": len(alerts) + len(resolved)}

    # ── 전송 ──
    def _send_one(self, name, conf, text):
        if name == "mattermost":
            data = {"text": text}
            for k in ("username", "channel", "icon_url"):
                if conf.get(k):
                    data[k] = conf[k]
            _post_json(conf["webhook_url"], data)
        elif name == "telegram":
            _post_json(f"https://api.telegram.org/bot{conf['bot_token']}/sendMessage",
                       {"chat_id": conf["chat_id"], "text": text, "disable_web_page_preview": True})

    def send(self, msg):
        """모든 받을 곳에 보내고 결과 {이름: "ok" 또는 에러} 를 돌려줘. 실패하면 한 번 더 시도."""
        results = {}
        for name, conf in self.channels().items():
            for attempt in (1, 2):
                try:
                    self._send_one(name, conf, msg["text"])
                    results[name] = "ok"
                    break
                except urllib.error.HTTPError as e:
                    results[name] = f"HTTP {e.code}"
                except Exception as e:
                    results[name] = type(e).__name__ + (f": {e.reason}" if hasattr(e, "reason") else "")
                if attempt == 1:
                    time.sleep(3)
        entry = {"t": time.time(), "kind": msg.get("kind"), "text": msg["text"],
                 "results": results, "ok": bool(results) and all(v == "ok" for v in results.values())}
        if not results:
            entry["results"] = {"-": "받을 곳 없음 (secrets.yaml)"}
        with self.lock:
            self.state["log"] = (self.state["log"] + [entry])[-10:]
            self._save()
        print("notify:", entry["kind"], entry["results"], flush=True)
        return entry

    def _worker(self):
        while True:
            msg = self.q.get()
            try:
                self.send(msg)
            except Exception as e:
                print("notify error:", repr(e), flush=True)

    def test(self):
        R = self.rules()
        text = (f"🔔 {self.title} 테스트 알림이야. 이게 보이면 연결 성공!\n"
                f"기준: 빨강 {_dur(R['bad_seconds'])} · 노랑 {_dur(R['warn_seconds'])} 넘게 계속되면 보내고, "
                f"같은 문제는 {R['cooldown_minutes']}분 동안 다시 안 보내.")
        return self.send({"text": text, "kind": "test"})

    # ── 화면용 상태 ──
    def status(self, now=None):
        now = now or time.time()
        R = self.rules()
        ch = self.channels()
        with self.lock:
            items = []
            quiet, qleft = self.in_quiet(now)
            for bid, a in self.state["active"].items():
                if a.get("held") and not a.get("gone_since"):
                    st, left = "held", qleft
                elif a.get("gone_since"):
                    st, left = "resolving", R["resolve_seconds"] - (now - a["gone_since"])
                elif a.get("sent_level") and RANK[a["level"]] <= RANK[a["sent_level"]]:
                    st, left = "sent", None
                else:
                    due_bad = a["bad_since"] + R["bad_seconds"] if a.get("bad_since") else None
                    due = min(x for x in (due_bad, a["first"] + R["warn_seconds"]) if x is not None)
                    cool = self.state["last_sent"].get(bid, 0) + R["cooldown_minutes"] * 60
                    if not a.get("sent_level"):
                        due = max(due, cool)
                    st, left = ("cooldown" if due == cool and cool > now else "waiting"), due - now
                items.append({"id": bid, "text": a["text"], "level": a["level"], "since": a["first"],
                              "state": st, "left": max(0, round(left)) if left is not None else None})
            log = list(self.state["log"][-5:])
            held_n = len(self.state["held"])
        qs = self.quiet_settings()
        return {"quiet": {**qs, "on": qs["enabled"], "now": quiet, "left": round(qleft) if qleft else None, "held": held_n},
                "enabled": self.enabled, "channels": {"mattermost": "mattermost" in ch, "telegram": "telegram" in ch},
                "rules": R, "items": sorted(items, key=lambda x: x["since"]), "log": log[::-1],
                "mute": self._c("mute", None) or [], "link": self._c("link", None) or ""}
