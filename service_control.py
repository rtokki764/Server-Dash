# ==========================================================
#  서비스 제어 모듈 (범용)
#  - 대시보드에서 서비스를 start / stop / restart / enable / disable
#  - system 서비스와 user 서비스(--user) 모두 지원
#  - 권한 흐름: 그냥 실행 → sudo -n → (안 되면) 비밀번호 입력창 → sudo -S
#  - 비밀번호는 그 요청 한 번에만 쓰고 어디에도 저장 안 함 (로그에도 안 남음)
#
#  다른 대시보드에 붙일 때:
#      from service_control import make_service_control
#      app.register_blueprint(make_service_control(서비스목록_함수, 설정dict))
#  서비스목록_함수는 [{"name":..., "unit":..., "scope": "system"|"user"}, ...] 를 돌려주면 됨
# ==========================================================

import logging
import shutil
import subprocess
import threading
import time

from flask import Blueprint, jsonify, request

log = logging.getLogger("service_control")

# 허용하는 동작 — 이 다섯 개 말고는 어떤 문자열도 systemctl로 안 간다
ALLOWED_ACTIONS = ("start", "stop", "restart", "enable", "disable")

# "권한 문제"로 보는 에러 문구들 (이게 보이면 다음 단계로 넘어감)
_PERM_HINTS = (
    "a password is required", "password is required",
    "permission denied", "access denied",
    "authentication is required", "interactive authentication required",
    "authentication required",
)


class _Lockout:
    """비밀번호 연속 실패 잠금 (IP별)."""

    def __init__(self, max_fails=3, lock_seconds=300):
        self.max_fails, self.lock_seconds = max_fails, lock_seconds
        self._fails, self._lock = {}, threading.Lock()

    def is_locked(self, ip):
        with self._lock:
            count, last = self._fails.get(ip, (0, 0))
            if count >= self.max_fails:
                if time.time() - last < self.lock_seconds:
                    return True
                del self._fails[ip]
            return False

    def record_fail(self, ip):
        with self._lock:
            count, _ = self._fails.get(ip, (0, 0))
            self._fails[ip] = (count + 1, time.time())

    def record_ok(self, ip):
        with self._lock:
            self._fails.pop(ip, None)


def _run(cmd, input_text=None, timeout=30):
    """명령 실행. 인자 리스트로만 실행해서 셸 주입이 안 됨."""
    try:
        p = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout: 명령이 %d초 안에 안 끝남" % timeout
    except FileNotFoundError as e:
        return -1, "", "not found: %s" % e


def _is_perm_error(stderr):
    s = (stderr or "").lower()
    return any(h in s for h in _PERM_HINTS)


class ServiceController:
    """systemctl 실행 로직. Flask와 분리해 둬서 테스트하기 쉬움."""

    def __init__(self, get_services, systemctl_path=None, sudo_path=None):
        self.get_services = get_services        # 호출할 때마다 최신 목록 (화면 추가분 포함)
        self.systemctl = systemctl_path or shutil.which("systemctl") or "systemctl"
        self.sudo = sudo_path or shutil.which("sudo") or "sudo"

    def find(self, name):
        for s in self.get_services():
            if s.get("name") == name:
                return s
        return None

    def _base(self, scope):
        return [self.systemctl, "--user"] if scope == "user" else [self.systemctl]

    def status(self, name):
        svc = self.find(name)
        if not svc or svc.get("scope") not in ("system", "user"):
            return None
        unit = svc.get("unit") or name
        base = self._base(svc["scope"])
        _, active, _ = _run(base + ["is-active", unit])
        _, enabled, _ = _run(base + ["is-enabled", unit])
        return {"name": name, "unit": unit, "scope": svc["scope"],
                "active": active or "unknown", "enabled": enabled or "unknown"}

    def control(self, name, action, password=None):
        """돌려주는 것: (HTTP 상태코드, 결과 dict)"""
        if action not in ALLOWED_ACTIONS:
            return 400, {"ok": False, "error": "허용되지 않은 동작: %s" % action}
        svc = self.find(name)
        if not svc:
            return 404, {"ok": False, "error": "목록에 없는 서비스: %s" % name}
        scope = svc.get("scope")
        if scope not in ("system", "user"):
            return 400, {"ok": False, "error": "systemd 서비스가 아니라서 제어할 수 없음 (scope: %s)" % scope}
        unit = svc.get("unit") or name

        # user 서비스: --user 로 바로. 비밀번호 필요 없음
        if scope == "user":
            rc, out, err = _run(self._base("user") + [action, unit])
            if rc == 0:
                return 200, {"ok": True, "via": "user", "detail": out or "완료"}
            return 500, {"ok": False, "via": "user", "error": err or out or "실패 (rc=%d)" % rc}

        # system 서비스 1단계: 그냥 실행 (root거나 polkit 허용이면 성공)
        rc, out, err = _run([self.systemctl, action, unit])
        if rc == 0:
            return 200, {"ok": True, "via": "system", "detail": out or "완료"}
        if not _is_perm_error(err) and rc != 1:
            return 500, {"ok": False, "via": "system", "error": err or out or "실패"}

        # 2단계: sudo -n (비번 없는 sudo 규칙이 있으면 성공)
        rc, out, err = _run([self.sudo, "-n", self.systemctl, action, unit])
        if rc == 0:
            return 200, {"ok": True, "via": "sudo", "detail": out or "완료"}

        if password is None:
            # 3단계로 가려면 비밀번호가 필요 → 프론트가 입력창을 띄움
            return 401, {"ok": False, "need_password": True,
                         "error": "관리자 권한이 필요해. 비밀번호를 입력해 줘."}

        # 3단계: sudo -S (비밀번호는 stdin으로만 전달)
        rc, out, err = _run([self.sudo, "-S", "-k", "-p", "", self.systemctl, action, unit],
                            input_text=password + "\n")
        password = None
        if rc == 0:
            return 200, {"ok": True, "via": "sudo", "detail": out or "완료"}
        low = (err or "").lower()
        if _is_perm_error(err) or "try again" in low or "incorrect" in low or "sorry" in low:
            return 403, {"ok": False, "bad_password": True,
                         "error": "비밀번호가 틀렸거나 sudo 권한이 없어."}
        return 500, {"ok": False, "via": "sudo", "error": err or out or "실패"}


def _client_ip():
    return (request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For", request.remote_addr or "?")).split(",")[0].strip()


def _secure_enough():
    """비밀번호를 받아도 되는 조건: HTTPS 이거나 localhost (터널 뒤면 remote_addr가 127.0.0.1)."""
    fwd = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()   # "https, http" 처럼 여러 값이 와도 첫 값
    if request.is_secure or fwd == "https":
        return True
    return (request.remote_addr or "") in ("127.0.0.1", "::1")


def make_service_control(get_services, cfg=None, controller=None):
    """Blueprint 만들기. cfg["control"] 섹션을 읽음 (없으면 기본값 = 꺼짐)."""
    c = (cfg or {}).get("control") or {}
    enabled = bool(c.get("enabled", False))
    ctl = controller or ServiceController(get_services)
    lockout = _Lockout(max_fails=int(c.get("max_password_fails", 3)),
                       lock_seconds=int(c.get("lock_seconds", 300)))

    bp = Blueprint("service_control", __name__, url_prefix="/service-control")

    @bp.get("/api/services/<name>")
    def one(name):
        st = ctl.status(name)
        if st is None:
            return jsonify({"ok": False, "error": "제어할 수 없는 서비스"}), 404
        return jsonify({"ok": True, "service": st, "control_enabled": enabled})

    @bp.post("/api/services/<name>/<action>")
    def control(name, action):
        ip = _client_ip()
        if not enabled:
            return jsonify({"ok": False, "error": "제어 기능이 꺼져 있어 (config.yaml control.enabled)"}), 403

        body = request.get_json(silent=True) or {}
        password = body.get("password")
        if password is not None:
            if not _secure_enough():
                seen = request.headers.get("X-Forwarded-Proto") or "없음"
                return jsonify({"ok": False, "error": "비밀번호는 HTTPS에서만 보낼 수 있어 (앱이 본 값: X-Forwarded-Proto=%s, 접속 IP=%s)" % (seen, request.remote_addr or "?")}), 400
            if lockout.is_locked(ip):
                return jsonify({"ok": False, "error": "실패가 반복돼서 잠깐 잠갔어. 5분 뒤에 다시 해 줘."}), 429

        log.info("control: ip=%s service=%s action=%s pw=%s", ip, name, action, "yes" if password else "no")
        status_code, result = ctl.control(name, action, password=password)
        if result.get("bad_password"):
            lockout.record_fail(ip)
        elif password is not None and result.get("ok"):
            lockout.record_ok(ip)
        log.info("control result: service=%s action=%s ok=%s via=%s",
                 name, action, result.get("ok"), result.get("via"))
        return jsonify(result), status_code

    return bp
