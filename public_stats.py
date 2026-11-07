# -*- coding: utf-8 -*-
"""공개 사이트 통계 /pub/stats (2026-11-07, W-20261107-17): MR /stats와 같은 출처를 서버에서 집계해 숫자만.

- 사람·계정·IP·지역·기기·사람별 기록은 계산도 안 하고 내보내지도 않는다(아래 SQL에 person·username·ip·device 없음).
- 소수 노출 방지: 재생 수 MIN_PLAYS(3)회 미만 작품은 순위에서 빼고, 종류는 '기타'로 합침. 인기 순위는 두 명 이상 본 것만(MR 공개 인기와 같은 기준).
- 켜진 카드만 계산·전달(꺼진 카드는 데이터 자체가 없음). 설정은 data/pub_stats_cards.json, 기본은 인기 순위·일별 재생·종류별 비율.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import watch_stats

MIN_PLAYS = 3
CARDS = [("popular", "인기 작품 순위"), ("daily", "일별 재생 수"), ("types", "종류별 비율"),
         ("hourly", "시간대별 재생"), ("playback", "재생 방식(원본/변환)"), ("completion", "끝까지 본 비율")]
DEFAULT_ON = ["popular", "daily", "types"]


def load_cards(path: Path) -> list[str]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        on = d.get("enabled") if isinstance(d, dict) else None
    except (OSError, ValueError):
        on = None
    keys = [k for k, _ in CARDS]
    return [k for k in keys if k in on] if isinstance(on, list) else list(DEFAULT_ON)


def save_cards(path: Path, enabled: list[str]) -> list[str]:
    keys = [k for k, _ in CARDS]
    on = [k for k in keys if k in (enabled or [])]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"enabled": on}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return on


def _popular(cur, days: int) -> list[dict]:
    rows = watch_stats.popularity(cur, days, limit=30, min_viewers=2)
    return [{"content": str(r["content"])[:80], "plays": int(r["plays"])} for r in rows if int(r["plays"]) >= MIN_PLAYS][:15]


def _types(cur, days: int, kind_of) -> list[dict]:
    """종류(드라마·예능·애니·영화…)별 재생 비율. 종류는 공개 목록(public_catalog)의 라이브러리 종류로 맞춤"""
    w, args = watch_stats._where(days)
    cur.execute(f"""SELECT media_type, COALESCE(series_name, title) AS name, COUNT(*) FROM v_play_history WHERE {w} GROUP BY 1, 2""", args)
    sums: dict[str, int] = {}
    for media_type, name, n in cur.fetchall():
        k = kind_of(str(name or ""), media_type) or ("영화" if media_type == "movie" else "기타")
        sums[k] = sums.get(k, 0) + int(n)
    total = sum(sums.values()) or 1
    out, etc = [], 0
    for k, n in sorted(sums.items(), key=lambda x: -x[1]):
        if n < MIN_PLAYS or k == "기타":
            etc += n
        else:
            out.append({"kind": k, "plays": n, "pct": round(100 * n / total)})
    if etc:
        out.append({"kind": "기타", "plays": etc, "pct": round(100 * etc / total)})
    return out


def build(cur, cards: list[str], days: int = 30, kind_of=None) -> dict:
    out: dict = {"days": days}
    if "popular" in cards:
        out["popular7"] = _popular(cur, 7)
        out["popular30"] = _popular(cur, 30)
    if "daily" in cards:
        w, args = watch_stats._where(days)
        cur.execute(f"""SELECT {watch_stats.KST_DAY}::text AS day, COUNT(*) FROM v_play_history WHERE {w} GROUP BY 1 ORDER BY 1""", args)
        out["daily"] = [{"day": d, "plays": int(n)} for d, n in cur.fetchall()]
    if "types" in cards:
        out["types"] = _types(cur, days, kind_of or (lambda name, mt: ""))
    if "hourly" in cards:
        w, args = watch_stats._where(days)
        cur.execute(f"""SELECT EXTRACT(HOUR FROM played_at AT TIME ZONE 'Asia/Seoul')::int, COUNT(*) FROM v_play_history
            WHERE {w} GROUP BY 1""", args)
        hours = [0] * 24
        for h, n in cur.fetchall():
            hours[int(h)] = int(n)
        out["hourly"] = hours
    if "playback" in cards:
        pb = watch_stats.playback(cur, days, private=False)       # private=False → 기기·사람 목록은 계산 안 함
        methods = [(str(m.get("method") or ""), float(m.get("hours") or 0)) for m in pb.get("methods") or []]
        total = sum(h for _, h in methods) or 1
        direct = sum(h for m, h in methods if m.lower() in ("directplay", "direct play", "directstream", "direct stream", "copy", "direct"))
        out["playback"] = {"direct_pct": round(100 * direct / total), "transcode_pct": 100 - round(100 * direct / total),
                           "since": pb.get("since")}
    if "completion" in cards:
        c = watch_stats.completion(cur, days, min_views=MIN_PLAYS)
        out["completion"] = {"avg_pct": c.get("avg_pct"), "finish_pct": c.get("finish_pct"),
                             "best": [{"content": str(r["content"])[:80], "avg_pct": r["avg_pct"]} for r in c.get("best") or []][:8]}
    return out
