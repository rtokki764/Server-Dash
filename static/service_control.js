/* ==========================================================
 * 서비스 제어 dialog (범용)
 *  - 서비스 행의 "관리" 버튼 → dialog에서 Start/Stop/Restart/Enable/Disable
 *  - 확인 단계 → 권한 부족하면 비밀번호 입력창 → 결과 토스트
 *  - 쓰는 쪽에서는 SC_OPEN("서비스이름") 만 부르면 됨
 *  - 색은 CSS 변수(--sc-accent, --sc-danger)로 바꿀 수 있음
 * ========================================================== */
(function () {
  "use strict";

  var API = "service-control/api/services";   // 상대경로: 하위 경로·프록시 뒤에서도 동작
  var LABEL = { start: "Start", stop: "Stop", restart: "Restart", enable: "Enable", disable: "Disable" };
  var DANGER = { stop: true, disable: true };

  var css = [
    ":root{--sc-accent:#3b82f6;--sc-danger:#dc2626;}",
    ".sc-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:9999;}",
    ".sc-dialog{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:12px;min-width:300px;max-width:92vw;padding:18px;box-shadow:0 10px 40px rgba(0,0,0,.5);font:14px/1.5 system-ui,sans-serif;}",
    ".sc-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;}",
    ".sc-title{font-size:16px;font-weight:600;}",
    ".sc-x{background:none;border:none;color:#94a3b8;font-size:20px;cursor:pointer;line-height:1;padding:4px;}",
    ".sc-x:hover{color:#e2e8f0;}",
    ".sc-status{font-size:13px;color:#94a3b8;margin-bottom:12px;}",
    ".sc-status b{color:#e2e8f0;}",
    ".sc-btns{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;}",
    ".sc-btn{padding:8px 0;border:none;border-radius:8px;cursor:pointer;font-size:14px;background:#334155;color:#e2e8f0;}",
    ".sc-btn:hover{filter:brightness(1.2);}",
    ".sc-btn.sc-danger{background:var(--sc-danger);color:#fff;}",
    ".sc-hide{margin-top:14px;padding-top:12px;border-top:1px solid #334155;}",
    ".sc-hide .sc-btn{padding:8px 14px;}",
    ".sc-extra{margin-top:12px;padding-top:11px;border-top:1px solid #334155;}",
    ".sc-row{display:flex;gap:8px;margin-top:10px;}",
    ".sc-row .sc-btn{flex:1;}",
    ".sc-extra input{width:100%;box-sizing:border-box;padding:8px;border-radius:6px;border:1px solid #475569;background:#0f172a;color:#e2e8f0;margin-top:8px;font-size:14px;}",
    ".sc-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-left:4px solid var(--sc-accent);padding:10px 18px;border-radius:8px;z-index:10000;font-size:14px;max-width:80vw;}",
    ".sc-toast.sc-err{border-left-color:var(--sc-danger);}"
  ].join("\n");
  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  function toast(msg, isErr, onDone) {
    var t = document.createElement("div");
    t.className = "sc-toast" + (isErr ? " sc-err" : "");
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); if (onDone) onDone(); }, 4000);
  }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (opts.body) opts.headers["Content-Type"] = "application/json";
    return fetch(path, opts).then(function (r) {
      return r.text().then(function (t) {
        var j;
        try { j = JSON.parse(t); }
        catch (e) {
          var hint = /<html|<!doctype/i.test(t) ? "서버가 JSON 대신 웹페이지를 보냈어 (로그인 만료·프록시·옛 버전 확인)" : "서버 응답을 읽을 수 없어";
          throw new Error(hint + " [HTTP " + r.status + "]");
        }
        j._status = r.status; return j;
      });
    });
  }

  function openDialog(name, hideName, manual) {
    var overlay = document.createElement("div");
    overlay.className = "sc-overlay";
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    document.addEventListener("keydown", onKey);
    var dlg = document.createElement("div");
    dlg.className = "sc-dialog";
    overlay.appendChild(dlg);
    document.body.appendChild(overlay);

    var current = null;
    var closed = false;

    function onKey(e) { if (e.key === "Escape") close(); }
    function close() {
      if (closed) return;                    // 토스트 끝날 때 닫기 + 사용자가 먼저 닫기 중복 방지
      closed = true;
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      if (window.SC_REFRESH) window.SC_REFRESH();
    }

    function render(extra) {
      dlg.innerHTML = "";
      var head = document.createElement("div");
      head.className = "sc-head";
      var title = document.createElement("div");
      title.className = "sc-title";
      title.textContent = name;
      var x = document.createElement("button");
      x.className = "sc-x";
      x.setAttribute("aria-label", "닫기");
      x.textContent = "✕";
      x.addEventListener("click", close);
      head.appendChild(title); head.appendChild(x);
      dlg.appendChild(head);

      var s = document.createElement("div");
      s.className = "sc-status";
      if (manual) {
        s.textContent = "수동 실행 서비스라 시작/중지는 여기서 못 해";
      } else if (current) {
        s.innerHTML = "상태: <b>" + current.active + "</b> · 자동시작: <b>" + current.enabled +
          "</b> · " + (current.scope === "user" ? "user 서비스" : "system 서비스");
      } else {
        s.textContent = "상태 불러오는 중...";
      }
      dlg.appendChild(s);

      var btns = document.createElement("div");
      btns.className = "sc-btns";
      (manual ? [] : ["start", "stop", "restart", "enable", "disable"]).forEach(function (a) {
        var b = document.createElement("button");
        b.className = "sc-btn" + (DANGER[a] ? " sc-danger" : "");
        b.textContent = LABEL[a];
        b.addEventListener("click", function () { askConfirm(a); });
        btns.appendChild(b);
      });
      if (!manual) dlg.appendChild(btns);
      if (extra) { extra.classList.add("sc-extra"); dlg.appendChild(extra); }
      if (hideName) {                       // 서비스 목록에서 치우기 (구분선 아래 따로)
        var hz = document.createElement("div");
        hz.className = manual ? "" : "sc-hide";
        var hb = document.createElement("button");
        hb.className = "sc-btn";
        hb.type = "button";
        hb.textContent = "목록외 +";
        hb.setAttribute("data-hide", hideName);
        hb.title = "서비스 목록에서 치워서 '목록에 없는 서비스' 칸으로 보내";
        hb.addEventListener("click", function () { setTimeout(close, 0); });   // 실제 처리는 화면의 data-hide 공통 핸들러가 함
        hz.appendChild(hb);
        dlg.appendChild(hz);
      }
    }

    function refresh(extra) {
      if (manual) { render(extra || null); return; }
      api(API + "/" + encodeURIComponent(name)).then(function (j) {
        current = j.service || null;
        render(extra || null);
      }).catch(function () { render(extra || null); });
    }

    function askConfirm(action) {
      var box = document.createElement("div");
      box.textContent = "정말 " + LABEL[action] + " 할까?";
      var row = document.createElement("div");
      row.className = "sc-row";
      var ok = document.createElement("button");
      ok.className = "sc-btn" + (DANGER[action] ? " sc-danger" : "");
      ok.textContent = "실행";
      ok.addEventListener("click", function () { run(action, null); });
      var no = document.createElement("button");
      no.className = "sc-btn";
      no.textContent = "취소";
      no.addEventListener("click", function () { render(null); });
      row.appendChild(ok); row.appendChild(no); box.appendChild(row);
      render(box);
    }

    function askPassword(action, msg) {
      var box = document.createElement("div");
      box.textContent = msg || "관리자 권한이 필요해. 비밀번호를 입력해 줘.";
      var input = document.createElement("input");
      input.type = "password";
      input.autocomplete = "current-password";
      input.placeholder = "sudo 비밀번호";
      box.appendChild(input);
      var row = document.createElement("div");
      row.className = "sc-row";
      var ok = document.createElement("button");
      ok.className = "sc-btn";
      ok.textContent = "확인";
      function submit() { var pw = input.value; input.value = ""; if (pw) run(action, pw); }
      ok.addEventListener("click", submit);
      input.addEventListener("keydown", function (e) { if (e.key === "Enter") submit(); });
      var no = document.createElement("button");
      no.className = "sc-btn";
      no.textContent = "취소";
      no.addEventListener("click", function () { render(null); });
      row.appendChild(ok); row.appendChild(no); box.appendChild(row);
      render(box);
      input.focus();
    }

    function run(action, password) {
      var busy = document.createElement("div");
      busy.textContent = LABEL[action] + " 실행 중...";
      render(busy);
      api(API + "/" + encodeURIComponent(name) + "/" + action, {
        method: "POST",
        body: JSON.stringify(password ? { password: password } : {})
      }).then(function (j) {
        password = null;
        if (j.ok) {
          // 성공 토스트가 사라질 때 관리 창도 같이 닫히게 예약 (버튼 누르자마자 닫지 않음)
          toast(name + " " + LABEL[action] + " 성공 (" + (j.via || "?") + ")", false, close);
          refresh();                                        // 토스트 보이는 동안 창에는 최신 상태 표시
          if (window.SC_REFRESH) window.SC_REFRESH();       // 메인 표도 바로 갱신
        }
        else if (j.need_password) { askPassword(action); }
        else if (j.bad_password) { askPassword(action, "비밀번호가 틀렸어. 다시 입력해 줘."); toast(j.error || "비밀번호 오류", true); }
        else { toast(j.error || "실패", true); refresh(); }
      }).catch(function (e) { toast("요청 실패: " + e, true); refresh(); });
    }

    refresh();
  }

  window.SC_OPEN = openDialog;
})();
