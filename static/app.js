// Media Router 공통 스크립트 (2026-10-16 레이아웃 개편)
// 모든 페이지에서 쓰는 알림(showToast)과 PC 제어 시트(켜기/상태/끄기/인코딩 시작).
// 예전에는 템플릿마다 같은 함수가 복사돼 있었다.

function showToast(message, type) {
  var toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.className = 'toast show' + (type ? ' ' + type : '');
  clearTimeout(toast._hideTimer);
  toast._hideTimer = setTimeout(function () { toast.className = 'toast'; }, 4000);
}

function mrJson(r) {
  return r.json().then(function (data) { return { ok: r.ok, data: data }; });
}

function mrBusy(btn, label) {
  var original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> ' + label;
  return function () { btn.disabled = false; btn.innerHTML = original; };
}

// ── PC 상태 표시 ───────────────────────────────────────
// 상태 확인은 PC의 SSH 포트에 접속해 보는 것이라 꺼져 있으면 2초쯤 걸린다.
// 페이지를 옮길 때마다 묻지 않도록 결과를 1분 동안 sessionStorage에 둔다.
var MR_PC_KEY = 'mrPcStatus';

function mrSetPcState(state) {
  var dot = document.getElementById('pcDot');
  var sheetDot = document.getElementById('pcSheetDot');
  var chipText = document.getElementById('pcChipText');
  var stateText = document.getElementById('pcStateText');
  var label = { on: 'PC 켜짐', off: 'PC 꺼짐', checking: '확인 중', unknown: 'PC 상태' }[state] || 'PC 상태';
  [dot, sheetDot].forEach(function (el) { if (el) el.className = 'pc-dot ' + (state === 'unknown' ? '' : state); });
  if (chipText) chipText.textContent = label;
  if (stateText) stateText.textContent = state === 'unknown' ? '알 수 없음' : label;
  var chip = document.getElementById('pcChip');
  if (chip) chip.setAttribute('aria-label', 'PC 제어 열기 (' + label + ')');
}

function checkPcStatus(silent) {
  var btn = document.getElementById('pcStatusBtn');
  if (btn) btn.disabled = true;
  mrSetPcState('checking');
  return fetch('/pc_status', { method: 'GET' })
    .then(mrJson)
    .then(function (res) {
      if (res.ok && res.data.success) {
        var state = res.data.online ? 'on' : 'off';
        mrSetPcState(state);
        try { sessionStorage.setItem(MR_PC_KEY, JSON.stringify({ s: state, t: Date.now() })); } catch (e) {}
        if (!silent) showToast(res.data.online ? 'PC가 켜져 있어요' : 'PC가 꺼져 있어요', res.data.online ? 'success' : 'error');
      } else {
        mrSetPcState('unknown');
        if (!silent) showToast(res.data.message || '확인하지 못했어요', 'error');
      }
    })
    .catch(function (e) {
      mrSetPcState('unknown');
      if (!silent) showToast('요청 중 오류가 발생했어요: ' + e, 'error');
    })
    .finally(function () { if (btn) btn.disabled = false; });
}

function wakePc() {
  var btn = document.getElementById('wakePcBtn');
  var done = mrBusy(btn, '보내는 중');
  fetch('/wake_pc', { method: 'POST' })
    .then(mrJson)
    .then(function (res) {
      showToast(res.data.message || (res.ok ? '켜기 신호를 보냈어요' : '요청 실패'), res.ok ? 'success' : 'error');
      try { sessionStorage.removeItem(MR_PC_KEY); } catch (e) {}
    })
    .catch(function (e) { showToast('요청 중 오류가 발생했어요: ' + e, 'error'); })
    .finally(function () { setTimeout(done, 3000); });
}

// 1분 유예로 종료 예약(인코딩 화면의 'PC 종료 취소'로 되돌릴 수 있음).
// 인코딩 중이면 서버가 409로 알려주고, 한 번 더 확인받으면 force로 다시 보낸다.
function shutdownPc(force) {
  if (!force && !confirm('KCK-GAMING PC를 끌까요?\n1분 뒤에 꺼지고, 그 전에 인코딩 화면의 \'PC 종료 취소\'로 막을 수 있어요.')) return;
  var btn = document.getElementById('shutdownPcBtn');
  var done = mrBusy(btn, '요청 중');
  fetch('/shutdown_pc', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force: !!force }) })
    .then(mrJson)
    .then(function (res) {
      done();
      if (!res.ok && res.data.encoding) {
        if (confirm(res.data.message + '\n그래도 끌까요?')) shutdownPc(true);
        return;
      }
      showToast(res.data.message || (res.ok ? '요청 완료' : '요청 실패'), res.ok ? 'success' : 'error');
    })
    .catch(function (e) { done(); showToast('요청 중 오류가 발생했어요: ' + e, 'error'); });
}

function mrStartEncode() {
  if (!confirm('KCK-GAMING에서 인코딩을 시작할까요?\nPC가 꺼져 있으면 먼저 켜요.')) return;
  var btn = document.getElementById('sheetEncodeBtn');
  var done = mrBusy(btn, '요청 중');
  fetch('/run_encode', { method: 'POST' })
    .then(mrJson)
    .then(function (res) { showToast(res.data.message || (res.ok ? '요청 완료' : '요청 실패'), res.ok ? 'success' : 'error'); })
    .catch(function (e) { showToast('요청 중 오류가 발생했어요: ' + e, 'error'); })
    .finally(function () { setTimeout(done, 2000); });
}

// ── PC 제어 시트 열기/닫기 ─────────────────────────────
function openPcSheet() {
  var sheet = document.getElementById('pcSheet');
  if (!sheet) return;
  sheet.classList.add('open');
  sheet.setAttribute('aria-hidden', 'false');
  var first = document.getElementById('wakePcBtn');
  if (first) first.focus();
}

function closePcSheet() {
  var sheet = document.getElementById('pcSheet');
  if (!sheet) return;
  sheet.classList.remove('open');
  sheet.setAttribute('aria-hidden', 'true');
  var chip = document.getElementById('pcChip');
  if (chip) chip.focus();
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    var sheet = document.getElementById('pcSheet');
    if (sheet && sheet.classList.contains('open')) closePcSheet();
  }
});

(function () {
  if (!document.getElementById('pcChip')) return;
  var cached = null;
  try { cached = JSON.parse(sessionStorage.getItem(MR_PC_KEY) || 'null'); } catch (e) {}
  if (cached && Date.now() - cached.t < 60000) mrSetPcState(cached.s);
  else checkPcStatus(true);
})();

// ── 화면 테마 (2026-10-16 개편 3단계) ───────────────────
// 시스템 설정 따르기 → 라이트 → 다크 순서로 바뀐다. 고른 값은 이 브라우저에만 저장(localStorage).
// 실제 적용은 <html data-theme>이고, 첫 화면 깜빡임을 막는 초기 적용은 base.html <head>에 있다.
var MR_THEME_ICONS = {
  system: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="8"></circle><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none"></path></svg>',
  light: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"></path></svg>',
  dark: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"></path></svg>'
};
var MR_THEME_LABEL = { system: '시스템 설정 따름', light: '라이트', dark: '다크' };

function mrGetTheme() {
  try { var t = localStorage.getItem('mrTheme'); return (t === 'light' || t === 'dark') ? t : 'system'; } catch (e) { return 'system'; }
}

function mrApplyTheme(mode) {
  if (mode === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', mode);
  var btn = document.getElementById('themeBtn');
  if (btn) {
    btn.innerHTML = MR_THEME_ICONS[mode];
    btn.setAttribute('aria-label', '화면 테마: ' + MR_THEME_LABEL[mode] + ' (눌러서 바꾸기)');
    btn.title = '화면 테마: ' + MR_THEME_LABEL[mode];
  }
}

function cycleTheme() {
  var next = { system: 'light', light: 'dark', dark: 'system' }[mrGetTheme()];
  try { if (next === 'system') localStorage.removeItem('mrTheme'); else localStorage.setItem('mrTheme', next); } catch (e) {}
  mrApplyTheme(next);
  showToast('화면 테마: ' + MR_THEME_LABEL[next]);
}

mrApplyTheme(mrGetTheme());
