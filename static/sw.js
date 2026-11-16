// 서버 대시보드 서비스 워커
// - "앱 설치" 아이콘이 뜨게 하려고 있는 최소 버전이야
// - 모니터링 화면은 항상 최신 값이어야 하니까 아무것도 저장(캐시)하지 않아
// - 서버에 연결이 안 될 때만 "연결 안 됨" 안내 화면을 보여줘
const OFFLINE = `<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>서버</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d0e10;color:#eceef1;
font:15px/1.6 -apple-system,"Segoe UI","Malgun Gothic",sans-serif;text-align:center}
b{font-size:18px}p{color:#8d9199}button{margin-top:10px;font:inherit;padding:8px 18px;border-radius:8px;
border:1px solid #24262a;background:#16171a;color:#eceef1}</style></head>
<body><div><b>서버에 연결할 수 없어</b><p>인터넷이나 서버, 터널(프록시) 상태를 확인해 줘</p>
<button onclick="location.reload()">다시 시도</button></div></body></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;      // 페이지 열 때만 관여, 나머지(API 등)는 그대로 통과
  e.respondWith(fetch(e.request).catch(() =>
    new Response(OFFLINE, {headers: {"Content-Type": "text/html; charset=utf-8"}})));
});
