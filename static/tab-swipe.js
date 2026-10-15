// 모바일에서 본문을 좌우로 스와이프하면 상단 .mode-tabs의 이전/다음 탭으로 이동.
// 왼쪽으로 밀면 다음 탭, 오른쪽으로 밀면 이전 탭. 끝 탭에서는 넘어가지 않음.
// 가로 스크롤 영역(표, 로그 박스, 탭 바 자체)이나 입력칸 위에서 시작한 스와이프는 무시한다.
(function () {
  if (!('ontouchstart' in window) || !window.matchMedia('(pointer: coarse)').matches) return;

  var MIN_DX = 80;       // 이만큼 가로로 움직여야 탭 이동
  var HINT_DX = 30;      // 이만큼부터 이동 예정 탭 안내 표시

  var tabs = Array.prototype.slice.call(document.querySelectorAll('.mode-tabs a'));
  if (tabs.length < 2) return;
  var cur = -1;
  for (var i = 0; i < tabs.length; i++) {
    if (tabs[i].classList.contains('active')) { cur = i; break; }
  }
  if (cur < 0) return;

  var hint = document.createElement('div');
  hint.style.cssText = 'position:fixed;top:50%;z-index:9999;transform:translateY(-50%);' +
    'padding:10px 14px;border-radius:999px;font-size:14px;font-weight:700;pointer-events:none;' +
    'background:rgba(0,0,0,.72);color:#fff;opacity:0;transition:opacity .12s,background .12s;' +
    'max-width:70vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
  document.body.appendChild(hint);

  function blocked(el) {
    for (; el && el !== document.body; el = el.parentElement) {
      if (el.matches('input, textarea, select, [contenteditable], .mode-tabs')) return true;
      if (el.scrollWidth > el.clientWidth + 1) {
        var ox = getComputedStyle(el).overflowX;
        if (ox === 'auto' || ox === 'scroll') return true;
      }
    }
    return false;
  }

  function target(dx) {
    var idx = dx < 0 ? cur + 1 : cur - 1;
    return idx >= 0 && idx < tabs.length ? tabs[idx] : null;
  }

  function hideHint() { hint.style.opacity = '0'; }

  var sx, sy, active = false, horizontal = null;

  document.addEventListener('touchstart', function (e) {
    active = e.touches.length === 1 && !blocked(e.target);
    if (!active) return;
    sx = e.touches[0].clientX; sy = e.touches[0].clientY;
    horizontal = null;
  }, { passive: true });

  document.addEventListener('touchmove', function (e) {
    if (!active) return;
    if (e.touches.length !== 1) { active = false; hideHint(); return; }
    var dx = e.touches[0].clientX - sx, dy = e.touches[0].clientY - sy;
    if (horizontal === null && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) {
      horizontal = Math.abs(dx) > Math.abs(dy) * 1.5;
      if (!horizontal) { active = false; return; }
    }
    if (!horizontal) return;
    var t = target(dx);
    if (!t || Math.abs(dx) < HINT_DX) { hideHint(); return; }
    hint.textContent = dx < 0 ? t.textContent.trim() + ' →' : '← ' + t.textContent.trim();
    hint.style.left = dx > 0 ? '12px' : '';
    hint.style.right = dx < 0 ? '12px' : '';
    hint.style.background = Math.abs(dx) >= MIN_DX ? 'rgba(37,99,235,.92)' : 'rgba(0,0,0,.72)';
    hint.style.opacity = '1';
  }, { passive: true });

  document.addEventListener('touchend', function (e) {
    if (!active) return;
    active = false;
    hideHint();
    if (!horizontal) return;
    var dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
    if (Math.abs(dx) < MIN_DX || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    if (window.getSelection && String(window.getSelection())) return;
    var t = target(dx);
    if (t) window.location.href = t.href;
  }, { passive: true });

  document.addEventListener('touchcancel', function () { active = false; hideHint(); }, { passive: true });

  // 뒤로가기(bfcache)로 돌아왔을 때 안내가 남아있지 않게
  window.addEventListener('pageshow', hideHint);
})();
