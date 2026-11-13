# -*- coding: utf-8 -*-
"""핫딜 — 텔레그램 내 사용자 계정으로 채널 읽기(Telethon, 2026-11-13). 읽기만 한다(쓰기·반응 금지).

- 로그인: MR 핫딜 > 채널 관리에서 전화번호 → 인증코드 → (2단계 비번). 예비로 터미널 hotdeal_login.py.
- API ID/HASH: 환경변수 TG_API_ID/TG_API_HASH(.env) 우선, 없으면 화면에서 넣은 값(data/hotdeal/api.json, 600).
- 세션: data/hotdeal/tg.session (600). 계정 전체 권한이 들어 있어 웹·로그에 절대 내보내지 않는다.
- 수집: MR 안 주기 작업(5분)에서 collect() 한 번 — 채널별 last_msg_id 뒤 것만, 처음은 최근 7일. FloodWait는 그 채널만 건너뜀.
"""
import asyncio
import json
import os
import threading
import time
from pathlib import Path

try:
    from telethon import TelegramClient, errors
    from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl, KeyboardButtonUrl
    HAVE_TELETHON = True
except ImportError:          # 테스트 환경 등
    HAVE_TELETHON = False

BACKFILL_DAYS = 7
_RUN_LOCK = threading.Lock()   # 세션 파일(sqlite)은 한 번에 하나만
PER_RUN_LIMIT = 300          # 한 번에 채널당 최대


class TG:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self.session = self.dir / "tg"            # Telethon이 tg.session을 만든다
        self.api_file = self.dir / "api.json"
        self._pending = {}                         # 로그인 중: phone, phone_code_hash

    # ── API 키
    def api(self):
        aid, ah = os.environ.get("TG_API_ID", "").strip(), os.environ.get("TG_API_HASH", "").strip()
        if not (aid and ah):
            try:
                d = json.loads(self.api_file.read_text(encoding="utf-8"))
                aid, ah = str(d.get("api_id") or ""), str(d.get("api_hash") or "")
            except (OSError, ValueError):
                pass
        return (int(aid), ah) if aid.isdigit() and ah else (None, None)

    def api_source(self) -> str:
        if os.environ.get("TG_API_ID") and os.environ.get("TG_API_HASH"):
            return "env"
        return "file" if self.api_file.exists() else ""

    def save_api(self, api_id: str, api_hash: str) -> None:
        fd = os.open(self.api_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"api_id": str(api_id).strip(), "api_hash": str(api_hash).strip()}, f)

    def _secure(self):
        for p in self.dir.glob("tg.session*"):
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass

    def _client(self):
        aid, ah = self.api()
        if not aid:
            raise RuntimeError("API ID/HASH가 없어요")
        if not HAVE_TELETHON:
            raise RuntimeError("telethon이 설치되지 않았어요")
        return TelegramClient(str(self.session), aid, ah, device_model="MR hotdeal", app_version="1.0",
                              receive_updates=False)

    def _run(self, coro_fn, timeout=120):
        """매번 새 이벤트 루프·클라이언트(스레드에서 불러도 안전). 세션 파일은 sqlite라 한 번에 하나만."""
        async def main():
            client = self._client()
            await client.connect()
            try:
                return await coro_fn(client)
            finally:
                await client.disconnect()
                self._secure()
        with _RUN_LOCK:
            return asyncio.run(asyncio.wait_for(main(), timeout))

    # ── 로그인
    def status(self) -> dict:
        aid, _ = self.api()
        out = {"api": bool(aid), "api_source": self.api_source(), "telethon": HAVE_TELETHON,
               "session": (self.dir / "tg.session").exists(), "authorized": False, "me": "",
               "pending": bool(self._pending)}
        if aid and HAVE_TELETHON and out["session"]:
            try:
                async def f(c):
                    if not await c.is_user_authorized():
                        return None
                    me = await c.get_me()
                    return (me.first_name or "") + (" @" + me.username if me.username else "")
                me = self._run(f, timeout=30)
                out["authorized"], out["me"] = me is not None, me or ""
            except Exception as e:
                out["error"] = type(e).__name__
        return out

    def send_code(self, phone: str) -> None:
        async def f(c):
            r = await c.send_code_request(phone)
            return r.phone_code_hash
        h = self._run(f, timeout=90)        # 한국 번호는 DC5로 옮겨 가며 느릴 수 있음(11-13)
        self._pending = {"phone": phone, "hash": h, "at": time.time()}

    def sign_in(self, code: str = "", password: str = "") -> str:
        """'ok' | 'password'(2단계 비번 필요). 실패는 예외"""
        p = dict(self._pending)
        async def f(c):
            if password:
                await c.sign_in(password=password)
                return "ok"
            if not p:
                raise RuntimeError("먼저 인증코드를 받아 주세요")
            try:
                await c.sign_in(phone=p["phone"], code=code, phone_code_hash=p["hash"])
            except errors.SessionPasswordNeededError:
                return "password"
            return "ok"
        r = self._run(f, timeout=60)
        if r == "ok":
            self._pending = {}
        return r

    def logout(self) -> None:
        try:
            async def f(c):
                if await c.is_user_authorized():
                    await c.log_out()
            self._run(f, timeout=30)
        except Exception:
            pass
        for p in self.dir.glob("tg.session*"):
            p.unlink(missing_ok=True)
        self._pending = {}

    # ── 채널 목록(내가 구독 중인 채널·그룹)
    def dialogs(self) -> list:
        async def f(c):
            out = []
            async for d in c.iter_dialogs(limit=400):
                if d.is_channel or d.is_group:
                    e = d.entity
                    out.append({"chat_id": str(d.id), "username": getattr(e, "username", "") or "",
                                "title": d.name or "", "broadcast": bool(getattr(e, "broadcast", False))})
            return out
        return self._run(f, timeout=90)

    # ── 수집
    def collect(self, channels: list, on_channel, log=lambda *a: None, budget: int = 240) -> dict:
        """channels: [{chat_id, username, last_msg_id, ...}] — 오래 못 가져온 채널부터 넣어 줄 것.
        채널 하나 끝날 때마다 on_channel(ch, msgs[(msg_id, ts, text, urls, link)], last_msg_id, error) → 바로 저장.
        (11-13) 처음 7일치를 다 가져온 뒤 한꺼번에 저장하다 280초를 넘겨 아무것도 안 남던 문제 → 채널별 저장, 시간(budget)이 지나면
        다음 채널은 다음 회차로. 반환 {chat_id: {"new": n, "last": id, "error": ""}}"""
        start = time.time()

        async def f(c):
            if not await c.is_user_authorized():
                raise RuntimeError("텔레그램 로그인이 필요해요")
            res = {}
            for ch in channels:
                if time.time() - start > budget:
                    log("시간이 다 돼 나머지 채널은 다음에")
                    break
                cid = ch["chat_id"]
                r = res[cid] = {"new": 0, "last": int(ch.get("last_msg_id") or 0), "error": ""}
                out = []
                try:
                    ent = await c.get_entity(int(cid))
                    since = time.time() - BACKFILL_DAYS * 86400
                    kw = {"limit": PER_RUN_LIMIT}
                    if r["last"]:
                        kw["min_id"] = r["last"]
                    msgs = []
                    async for m in c.iter_messages(ent, **kw):
                        if not r["last"] and m.date.timestamp() < since:
                            break
                        msgs.append(m)
                    un = ch.get("username") or getattr(ent, "username", "") or ""
                    for m in sorted(msgs, key=lambda x: x.id):
                        text = m.message or ""
                        urls = []
                        for e, t in (m.get_entities_text() if text else []):
                            if isinstance(e, MessageEntityTextUrl):
                                urls.append(e.url)
                            elif isinstance(e, MessageEntityUrl):
                                urls.append(t)
                        rm = getattr(m, "reply_markup", None)
                        for row in getattr(rm, "rows", None) or []:
                            for b in row.buttons:
                                if isinstance(b, KeyboardButtonUrl):
                                    urls.append(b.url)
                        if text or urls:
                            link = ("https://t.me/%s/%d" % (un, m.id)) if un else \
                                   ("https://t.me/c/%s/%d" % (str(cid).replace("-100", "", 1), m.id))
                            out.append((m.id, int(m.date.timestamp()), text, urls, link))
                        r["last"] = max(r["last"], m.id)
                except errors.FloodWaitError as e:
                    r["error"] = "잠시 막힘(FloodWait %d초) — 다음에 다시" % e.seconds
                    log("FloodWait", e.seconds)
                except Exception as e:
                    r["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
                try:
                    r["new"] = on_channel(ch, out, r["last"], r["error"]) or 0
                except Exception as e:
                    r["error"] = r["error"] or "저장 실패 %s" % type(e).__name__
                if r["error"].startswith("잠시 막힘"):
                    break
                await asyncio.sleep(1)                     # 채널 사이 쉼(계정 보호)
            return res
        return self._run(f, timeout=budget + 240)
