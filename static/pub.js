/* 공개 사이트(request.kckworld.com) — 11-07 W-14
   1) 가로 줄 ◀ ▶ 버튼(마우스 있는 기기만), 2) 포스터 누르면 설명 창(<dialog>, 서버가 그린 조각을 같은 사이트에서 받아 넣음).
   이 파일이 없어도 가로 스크롤·링크(따로 화면)는 그대로 동작한다. */
(function () {
  "use strict";
  // ── 가로 줄 ──
  document.querySelectorAll("[data-row]").forEach(function (row) {
    var strip = row.querySelector(".strip"), prev = row.querySelector(".prev"), next = row.querySelector(".next");
    if (!strip || !prev || !next) return;
    function sync() {
      var max = strip.scrollWidth - strip.clientWidth - 2;
      prev.hidden = next.hidden = max <= 0;
      prev.disabled = strip.scrollLeft <= 2;
      next.disabled = strip.scrollLeft >= max;
    }
    [prev, next].forEach(function (b) {
      b.addEventListener("click", function () {
        strip.scrollBy({ left: Number(b.dataset.dir) * Math.max(strip.clientWidth * 0.85, 200), behavior: "smooth" });
      });
    });
    strip.addEventListener("scroll", sync, { passive: true });
    window.addEventListener("resize", sync);
    sync();
  });

  // ── 설명 창 ──
  var dlg = document.getElementById("tiDlg"), body = document.getElementById("tiBody");
  if (!dlg || typeof dlg.showModal !== "function") return;           // 오래된 브라우저는 그냥 링크로
  var lastFocus = null, seq = 0;
  function close() { if (dlg.open) dlg.close(); }
  dlg.addEventListener("close", function () { body.innerHTML = ""; if (lastFocus) lastFocus.focus(); });
  dlg.addEventListener("click", function (e) {
    if (e.target === dlg || e.target.closest("[data-close]")) close();   // 바깥(배경) 또는 × 누르면 닫기
  });
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-title]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;   // 새 탭으로 열기는 그대로
    e.preventDefault();
    lastFocus = a;
    var my = ++seq;
    body.innerHTML = '<p class="muted" style="padding:20px 4px;">불러오는 중…</p>';
    dlg.showModal();
    fetch(a.getAttribute("href") + "&frag=1", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) { if (my === seq) body.innerHTML = html; })            // 서버가 이스케이프해서 그린 조각
      .catch(function () { if (my === seq) body.innerHTML = '<p class="muted" style="padding:20px 4px;">불러오지 못했어요.</p>'; });
  });
})();
