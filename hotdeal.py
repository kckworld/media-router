# -*- coding: utf-8 -*-
"""핫딜 모아 보기 (2026-11-13) — 저장·파싱·검색. 텔레그램 수집은 hotdeal_tg.py.

내가 구독 중인 텔레그램 핫딜 채널 메시지를 한 양식(상품명·가격·쇼핑몰·바로가기·원문)으로 저장하고 검색한다.
LLM 없이 정규식·휴리스틱만. 파싱이 안 돼도 원문(raw)은 꼭 저장(원문 검색은 되게).

DB: data/hotdeal/hotdeal.db (MR 본 DB와 분리 — 5분마다 쓰는 일이 본 DB를 잠그지 않게, 지우기도 쉽게)
  hotdeal_channels(chat_id PK, username, title, enabled, last_msg_id, last_error, last_ok)
  hotdeals(id "<chat_id>_<msg_id>", chat_id, msg_id, title, price, shop, url, url_key, source, source_link, ts, raw, is_dup)
  hotdeals_fts — FTS5(title, raw, shop) tokenize='trigram' (3글자 이상 검색어). 2글자 이하·trigram 없음은 LIKE.
중복: 같은 url_key가 48시간 안에 있으면 is_dup=1. 보관 기간(기본 90일) 지난 것 자동 삭제.
"""
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DUP_WINDOW = 48 * 3600
_lock = threading.Lock()

# 쇼핑몰 판별: 도메인 끝부분 → 이름
SHOPS = [
    ("coupang.com", "쿠팡"), ("coupa.ng", "쿠팡"), ("link.coupang.com", "쿠팡"),
    ("smartstore.naver.com", "네이버"), ("brand.naver.com", "네이버"), ("shopping.naver.com", "네이버"),
    ("naver.me", "네이버"), ("naver.com", "네이버"),
    ("gmarket.co.kr", "G마켓"), ("auction.co.kr", "옥션"), ("11st.co.kr", "11번가"),
    ("ssg.com", "SSG"), ("emart.ssg.com", "SSG"), ("lotteon.com", "롯데온"), ("kurly.com", "컬리"),
    ("musinsa.com", "무신사"), ("29cm.co.kr", "29CM"), ("ohou.se", "오늘의집"), ("tmon.co.kr", "티몬"),
    ("wemakeprice.com", "위메프"), ("aliexpress.com", "알리"), ("a.aliexpress.com", "알리"), ("s.click.aliexpress.com", "알리"),
    ("temu.com", "테무"), ("amazon.com", "아마존"), ("amazon.co.jp", "아마존JP"), ("amzn.to", "아마존"),
    ("hmall.com", "현대H몰"), ("gsshop.com", "GS샵"), ("cjonstyle.com", "CJ온스타일"), ("oliveyoung.co.kr", "올리브영"),
    ("kakao.com", "카카오"), ("e-himart.co.kr", "하이마트"), ("lotteimall.com", "롯데아이몰"), ("danawa.com", "다나와"),
    ("ppomppu.co.kr", "뽐뿌"), ("quasarzone.com", "퀘이사존"), ("fmkorea.com", "에펨코리아"), ("ruliweb.com", "루리웹"),
]
# 트래킹 파라미터(중복 판별 열쇠에서 뺌)
TRACK = re.compile(r"^(utm_|fbclid|gclid|ref|src|source|campaign|affiliate|af_|click|traceid|clickid|lptag|subid|"
                   r"redirect|isAddedCart|itemsCount|searchId|rank|q$|wPcid|wRef|wTime|NaPm|nv_)", re.I)
PRICE = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d{3,8})\s*원")
URL = re.compile(r"https?://[^\s<>\"'\])]+")


# ───────────────────────────── 파싱 ─────────────────────────────
def shop_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for dom, name in SHOPS:
        if host == dom or host.endswith("." + dom):
            return name
    return host.removeprefix("www.").removeprefix("m.") if host else ""


def url_key(url: str) -> str:
    """중복 판별용: 트래킹 파라미터 빼고, 쿠팡은 productId(+vendorItemId), 네이버 스토어는 products/번호"""
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return url
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    m = re.search(r"/vp/products/(\d+)", u.path)
    if "coupang.com" in host and m:
        return "coupang:" + m.group(1)
    m = re.search(r"/products/(\d+)", u.path)
    if "naver" in host and m:
        return "naver:" + m.group(1)
    m = re.search(r"goodscode=(\d+)", u.query, re.I) or re.search(r"/item/(\d+)", u.path)
    if ("gmarket" in host or "auction" in host) and m:
        return host.split(".")[0] + ":" + m.group(1)
    m = re.search(r"/products/(\d+)", u.path)
    if "11st" in host and m:
        return "11st:" + m.group(1)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=False) if not TRACK.match(k)]
    return urlunsplit(("", host, u.path.rstrip("/"), urlencode(sorted(q)), "")).lstrip("/")


def _clean_line(s: str) -> str:
    s = URL.sub("", s)
    s = re.sub(r"[#*_`~|>\[\]【】]+", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -:·•[]")


def parse(text: str, urls=()) -> dict:
    """원문 + (entities·버튼에서 꺼낸) URL 목록 → {title, price, shop, url, url_key}. 못 찾으면 빈 값."""
    text = text or ""
    allurls = [u for u in list(urls) + URL.findall(text) if u]
    # 텔레그램 자체 링크(t.me)·채널 홍보 링크는 상품 링크가 아님
    goods = [u for u in allurls if not re.match(r"https?://(t\.me|telegram\.me|telegra\.ph)/", u)]
    url = goods[0] if goods else ""
    title = ""
    for line in text.splitlines():
        c = _clean_line(line)
        if len(c) >= 2 and not re.fullmatch(r"[\d,\s원%↓→\-()]+", c):
            title = c
            break
    prices = [int(p.replace(",", "")) for p in PRICE.findall(text)]
    prices = [p for p in prices if 100 <= p <= 100_000_000]
    price = min(prices) if prices else None          # '정가 39,900원 → 12,900원'이면 싼 쪽
    return {"title": title[:200], "price": price, "shop": shop_of(url) if url else "",
            "url": url, "url_key": url_key(url) if url else ""}


# 채널별 전용 파서(결과가 안 좋은 채널만 추가): chat_id 또는 username → 함수(text, urls) → dict
CHANNEL_PARSERS = {}


def parse_for(channel: dict, text: str, urls=()) -> dict:
    fn = CHANNEL_PARSERS.get(str(channel.get("chat_id"))) or CHANNEL_PARSERS.get((channel.get("username") or "").lower())
    out = parse(text, urls)
    if fn:
        try:
            out.update({k: v for k, v in (fn(text, urls) or {}).items() if v})
        except Exception:
            pass
    return out


# ───────────────────────────── DB ─────────────────────────────
class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fts = False
        with self._db() as c:
            c.execute("pragma journal_mode=wal")          # 5분마다 쓰는 동안에도 화면 검색이 막히지 않게
            c.executescript("""
            create table if not exists hotdeal_channels(chat_id text primary key, username text, title text,
                display text, enabled integer default 1, last_msg_id integer default 0, last_error text default '',
                last_ok integer default 0, added integer default 0);
            create table if not exists hotdeals(id text primary key, chat_id text, msg_id integer, title text,
                price integer, shop text, url text, url_key text, source text, source_link text, ts integer,
                raw text, is_dup integer default 0);
            create index if not exists hd_ts on hotdeals(ts);
            create index if not exists hd_key on hotdeals(url_key, ts);
            create table if not exists hotdeal_meta(k text primary key, v text);
            """)
            try:
                c.execute("create virtual table if not exists hotdeals_fts using fts5(id unindexed, title, raw, shop, tokenize='trigram')")
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False

    @contextmanager
    def _db(self):
        """쓰고 나면 꼭 닫음 — 안 닫은 연결이 잠금을 쥐고 있으면 다른 쓰기가 timeout만큼 기다림(11-13 시험에서 발견)"""
        c = sqlite3.connect(str(self.path), timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    # ── 설정·상태(작은 key/value)
    def meta(self, k: str, default=None):
        with self._db() as c:
            r = c.execute("select v from hotdeal_meta where k=?", (k,)).fetchone()
        if not r:
            return default
        try:
            return json.loads(r["v"])
        except ValueError:
            return default

    def set_meta(self, k: str, v) -> None:
        with _lock, self._db() as c:
            c.execute("insert or replace into hotdeal_meta(k, v) values(?, ?)", (k, json.dumps(v, ensure_ascii=False)))

    # ── 채널
    def channels(self, only_enabled=False) -> list:
        with self._db() as c:
            q = "select * from hotdeal_channels" + (" where enabled=1" if only_enabled else "") + " order by added"
            return [dict(r) for r in c.execute(q)]

    def add_channel(self, chat_id, username: str, title: str) -> None:
        with _lock, self._db() as c:
            c.execute("""insert into hotdeal_channels(chat_id, username, title, display, enabled, added)
                         values(?,?,?,?,1,?) on conflict(chat_id) do update set username=excluded.username, title=excluded.title""",
                      (str(chat_id), username or "", title or "", title or username or str(chat_id), int(time.time())))

    def update_channel(self, chat_id, **kw) -> bool:
        cols = {k: v for k, v in kw.items() if k in ("display", "enabled", "last_msg_id", "last_error", "last_ok")}
        if not cols:
            return False
        with _lock, self._db() as c:
            cur = c.execute("update hotdeal_channels set %s where chat_id=?" % ", ".join(k + "=?" for k in cols),
                            list(cols.values()) + [str(chat_id)])
            return cur.rowcount > 0

    def delete_channel(self, chat_id, with_deals=False) -> None:
        with _lock, self._db() as c:
            c.execute("delete from hotdeal_channels where chat_id=?", (str(chat_id),))
            if with_deals:
                ids = [r[0] for r in c.execute("select id from hotdeals where chat_id=?", (str(chat_id),))]
                c.execute("delete from hotdeals where chat_id=?", (str(chat_id),))
                if self.fts:
                    c.executemany("delete from hotdeals_fts where id=?", [(i,) for i in ids])

    # ── 딜
    def add_deal(self, ch: dict, msg_id: int, ts: int, text: str, urls=(), source_link: str = "") -> dict | None:
        """새로 넣었으면 저장한 dict, 이미 있으면 None"""
        did = "%s_%s" % (ch["chat_id"], msg_id)
        p = parse_for(ch, text, urls)
        with _lock, self._db() as c:
            if c.execute("select 1 from hotdeals where id=?", (did,)).fetchone():
                return None
            dup = 0
            if p["url_key"]:
                dup = 1 if c.execute("select 1 from hotdeals where url_key=? and ts>=? and ts<=? and id!=? limit 1",
                                     (p["url_key"], ts - DUP_WINDOW, ts, did)).fetchone() else 0
            row = dict(id=did, chat_id=str(ch["chat_id"]), msg_id=int(msg_id), title=p["title"], price=p["price"],
                       shop=p["shop"], url=p["url"], url_key=p["url_key"], source=ch.get("display") or ch.get("title") or "",
                       source_link=source_link, ts=int(ts), raw=(text or "")[:4000], is_dup=dup)
            c.execute("insert into hotdeals(%s) values(%s)" % (", ".join(row), ", ".join("?" * len(row))), list(row.values()))
            if self.fts:
                c.execute("insert into hotdeals_fts(id, title, raw, shop) values(?,?,?,?)", (did, row["title"], row["raw"], row["shop"]))
        return row

    def purge(self, keep_days: int) -> int:
        cut = int(time.time()) - max(1, int(keep_days)) * 86400
        with _lock, self._db() as c:
            ids = [r[0] for r in c.execute("select id from hotdeals where ts<?", (cut,))]
            c.execute("delete from hotdeals where ts<?", (cut,))
            if self.fts and ids:
                c.executemany("delete from hotdeals_fts where id=?", [(i,) for i in ids])
        return len(ids)

    def search(self, q: str = "", shop: str = "", chat_id: str = "", days: int = 0, sort: str = "new",
               hide_dup: bool = True, limit: int = 50, offset: int = 0) -> dict:
        where, args = [], []
        q = (q or "").strip()
        if q:
            words = [w for w in re.split(r"\s+", q) if w][:5]
            long_words = [w for w in words if len(w) >= 3]
            if self.fts and long_words:
                where.append("h.id in (select id from hotdeals_fts where hotdeals_fts match ?)")
                args.append(" AND ".join('"%s"' % w.replace('"', '""') for w in long_words))
            for w in words:
                if not (self.fts and len(w) >= 3):
                    where.append("(h.title like ? or h.raw like ? or h.shop like ?)")
                    args += ["%" + w + "%"] * 3
        if shop:
            where.append("h.shop=?")
            args.append(shop)
        if chat_id:
            where.append("h.chat_id=?")
            args.append(str(chat_id))
        if days:
            where.append("h.ts>=?")
            args.append(int(time.time()) - int(days) * 86400)
        if hide_dup:
            where.append("h.is_dup=0")
        w = (" where " + " and ".join(where)) if where else ""
        order = {"price": "h.price is null, h.price asc, h.ts desc", "price_desc": "h.price is null, h.price desc, h.ts desc"}.get(sort, "h.ts desc")
        with self._db() as c:
            total = c.execute("select count(*) from hotdeals h" + w, args).fetchone()[0]
            rows = [dict(r) for r in c.execute("select h.id, h.chat_id, h.title, h.price, h.shop, h.url, h.source, h.source_link, h.ts, h.raw, h.is_dup "
                                               "from hotdeals h" + w + " order by " + order + " limit ? offset ?",
                                               args + [max(1, min(200, int(limit))), max(0, int(offset))])]
            shops = [r[0] for r in c.execute("select shop from hotdeals where shop!='' group by shop order by count(*) desc limit 30")]
        return {"total": total, "items": rows, "shops": shops}

    def stats(self) -> dict:
        with self._db() as c:
            n = c.execute("select count(*), max(ts) from hotdeals").fetchone()
        return {"count": n[0], "latest": n[1]}


def match_keywords(row: dict, keywords) -> list:
    hay = ((row.get("title") or "") + "\n" + (row.get("raw") or "")).lower()
    return [k for k in keywords if k and k.lower() in hay]


def feed_text(row: dict, kw=None) -> str:
    price = format(row["price"], ",") + "원" if row.get("price") else ""
    head = ("🔔 [%s] " % ", ".join(kw) if kw else "🔥 ") + (row.get("title") or "(제목 없음)")
    meta = " · ".join(x for x in (price, row.get("shop") or "", row.get("source") or "") if x)
    lines = [head, meta]
    if row.get("url"):
        lines.append(row["url"])
    if row.get("source_link"):
        lines.append("원문: " + row["source_link"])
    return "\n".join(l for l in lines if l)
