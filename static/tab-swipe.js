// 모바일에서 본문을 좌우로 스와이프하면 메뉴 순서(#pageOrder, base.html)상 이전/다음 화면으로 이동.
// 왼쪽으로 밀면 다음 화면, 오른쪽으로 밀면 이전 화면. 처음/끝 화면에서는 넘어가지 않음.
// 가로 스크롤 영역(표, 로그 박스, 세부 탭 바)이나 입력칸, 하단 메뉴에서 시작한 스와이프는 무시한다.
// 상단 바와 세부 탭은 고정하고 <main>만 손가락을 따라 움직이며, 넘어가면 새 페이지의
// <main>이 반대쪽에서 밀려 들어온다(방향은 sessionStorage로 다음 페이지에 전달).
(function () {
  var KEY = 'tabSwipeIn';
  var MIN_DX = 80;       // 이만큼 가로로 움직여야 탭 이동
  var HINT_DX = 30;      // 이만큼부터 이동 예정 탭 안내 표시
  var OUT_MS = 180;      // 나가는 애니메이션
  var IN_MS = 260;       // 들어오는 애니메이션
  var EASE = 'cubic-bezier(.22,.61,.36,1)';

  var main = document.querySelector('main');
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var root = document.documentElement;

  function clearMain() {
    if (!main) return;
    main.style.transition = '';
    main.style.transform = '';
    main.style.opacity = '';
    main.style.willChange = '';
    root.style.overflowX = '';
  }

  // 스와이프로 넘어온 페이지면 본문을 옆에서 밀어 넣는다.
  // 이 스크립트는 </body> 직전에 있어서 첫 화면이 그려지기 전에 시작 위치를 잡을 수 있다.
  var incoming = null;
  try {
    incoming = JSON.parse(sessionStorage.getItem(KEY) || 'null');
    sessionStorage.removeItem(KEY);
  } catch (e) {}
  if (main && !reduce && incoming && Date.now() - incoming.t < 5000) {
    root.style.overflowX = 'hidden';
    main.style.transform = 'translateX(' + (incoming.dir === 'next' ? '' : '-') + '35%)';
    main.style.opacity = '0';
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        main.style.transition = 'transform ' + IN_MS + 'ms ' + EASE + ', opacity ' + IN_MS + 'ms ' + EASE;
        main.style.transform = 'translateX(0)';
        main.style.opacity = '1';
        setTimeout(clearMain, IN_MS + 50);
      });
    });
  }
  // 뒤로가기(bfcache)로 돌아오면 밀려나간 상태 그대로 복원되므로 원위치
  window.addEventListener('pageshow', function (e) { if (e.persisted) clearMain(); });

  // 세부 탭 바가 가로로 넘칠 때 현재 탭을 가운데로. 이전 페이지의 스크롤 위치에서 출발해
  // 부드럽게 옮겨서, 탭 바가 매번 맨 왼쪽에서 튀어오르지 않게 한다.
  var BAR_KEY = 'tabBarScroll';
  var bar = document.querySelector('.subtabs');
  var activeTab = bar && bar.querySelector('a.active');
  if (bar && activeTab && bar.scrollWidth > bar.clientWidth) {
    var prev = null;
    try {
      prev = JSON.parse(sessionStorage.getItem(BAR_KEY) || 'null');
      sessionStorage.removeItem(BAR_KEY);
    } catch (e) {}
    var br = bar.getBoundingClientRect(), ar = activeTab.getBoundingClientRect();
    var center = bar.scrollLeft + (ar.left - br.left) - (bar.clientWidth - ar.width) / 2;
    center = Math.max(0, Math.min(center, bar.scrollWidth - bar.clientWidth));
    if (prev && Date.now() - prev.t < 5000 && !reduce && bar.scrollTo) {
      bar.scrollLeft = prev.x;
      requestAnimationFrame(function () { bar.scrollTo({ left: center, behavior: 'smooth' }); });
    } else {
      bar.scrollLeft = center;
    }
  }
  function saveBar() {
    if (!bar) return;
    try { sessionStorage.setItem(BAR_KEY, JSON.stringify({ x: bar.scrollLeft, t: Date.now() })); } catch (e) {}
  }
  if (bar) bar.addEventListener('click', function (e) { if (e.target.closest('a')) saveBar(); });

  if (!('ontouchstart' in window) || !window.matchMedia('(pointer: coarse)').matches) return;

  var tabs = Array.prototype.slice.call(document.querySelectorAll('#pageOrder a'));
  if (tabs.length < 2) return;
  var cur = -1;
  for (var i = 0; i < tabs.length; i++) {
    if (tabs[i].classList.contains('active')) { cur = i; break; }
  }
  if (cur < 0) return;

  var hint = document.createElement('div');
  hint.style.cssText = 'position:fixed;top:50%;z-index:9999;transform:translateY(-50%);' +
    'padding:10px 14px;border-radius:999px;font-size:14px;font-weight:700;pointer-events:none;' +
    'background:rgba(22,24,29,.78);color:#fff;opacity:0;transition:opacity .12s,background .12s;' +
    'max-width:70vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
  document.body.appendChild(hint);

  function blocked(el) {
    for (; el && el !== document.body; el = el.parentElement) {
      if (el.matches('input, textarea, select, [contenteditable], .subtabs, .bottomnav, .sheet')) return true;
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

  // 손가락을 따라 본문 이동. 넘어갈 탭이 없는 쪽은 고무줄처럼 조금만 움직인다.
  function drag(dx) {
    if (!main || reduce) return;
    var x = target(dx) ? dx : dx * 0.25;
    root.style.overflowX = 'hidden';
    main.style.transition = 'none';
    main.style.willChange = 'transform';
    main.style.transform = 'translateX(' + x + 'px)';
    main.style.opacity = String(Math.max(0.4, 1 - Math.abs(x) / (window.innerWidth * 1.5)));
  }

  function snapBack() {
    if (!main || reduce) return;
    main.style.transition = 'transform ' + OUT_MS + 'ms ' + EASE + ', opacity ' + OUT_MS + 'ms ' + EASE;
    main.style.transform = 'translateX(0)';
    main.style.opacity = '1';
    setTimeout(clearMain, OUT_MS + 50);
  }

  function go(t, dx) {
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ dir: dx < 0 ? 'next' : 'prev', t: Date.now() }));
    } catch (e) {}
    saveBar();
    if (!main || reduce) { window.location.href = t.href; return; }
    main.style.transition = 'transform ' + OUT_MS + 'ms ' + EASE + ', opacity ' + OUT_MS + 'ms ' + EASE;
    main.style.transform = 'translateX(' + (dx < 0 ? '-' : '') + '100%)';
    main.style.opacity = '0';
    setTimeout(function () { window.location.href = t.href; }, OUT_MS);
  }

  var sx, sy, active = false, horizontal = null;

  document.addEventListener('touchstart', function (e) {
    active = e.touches.length === 1 && !blocked(e.target);
    if (!active) return;
    sx = e.touches[0].clientX; sy = e.touches[0].clientY;
    horizontal = null;
  }, { passive: true });

  document.addEventListener('touchmove', function (e) {
    if (!active) return;
    if (e.touches.length !== 1) { active = false; hideHint(); if (horizontal) snapBack(); return; }
    var dx = e.touches[0].clientX - sx, dy = e.touches[0].clientY - sy;
    if (horizontal === null && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) {
      horizontal = Math.abs(dx) > Math.abs(dy) * 1.5;
      if (!horizontal) { active = false; return; }
    }
    if (!horizontal) return;
    drag(dx);
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
    var t = target(dx);
    if (!t || Math.abs(dx) < MIN_DX || Math.abs(dx) < Math.abs(dy) * 1.5 ||
        (window.getSelection && String(window.getSelection()))) {
      snapBack();
      return;
    }
    go(t, dx);
  }, { passive: true });

  document.addEventListener('touchcancel', function () {
    if (active && horizontal) snapBack();
    active = false;
    hideHint();
  }, { passive: true });

  window.addEventListener('pageshow', hideHint);
})();
