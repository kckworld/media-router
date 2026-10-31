"""시청 통계 (2026-10-31, Grafana '미디어 시청 통계' 대시보드를 MR로 옮김)

media_stats Postgres의 v_play_history(·v_play_history_geo)를 대시보드와 같은 SQL로 읽는다(읽기만).
Grafana의 $__timeFilter(played_at)는 '최근 N일', $person·$service는 정확히 같은 값으로 바꿨다.
개인정보(사용자 이름·지역·재생 기록)는 관리자에게만 준다 - 보기 전용 계정과 공개 인기 페이지는 콘텐츠 통계만.
"""
from __future__ import annotations

CONTENT = ("CASE WHEN media_type = 'movie' THEN title ELSE COALESCE(series_name, title) || "
           "CASE WHEN season IS NOT NULL THEN ' S' || LPAD(season::text, 2, '0') ELSE '' END END")
KST_DAY = "(DATE(played_at AT TIME ZONE 'Asia/Seoul'))"


def _where(days: int, person: str = "", service: str = "", alias: str = "") -> tuple[str, list]:
    a = f"{alias}." if alias else ""
    sql, args = f"{a}played_at >= now() - make_interval(days => %s)", [days]
    if person:
        sql += f" AND {a}person_name = %s"
        args.append(person)
    if service:
        sql += f" AND {a}source = %s"
        args.append(service)
    return sql, args


def _rows(cur, sql: str, args: list) -> list[dict]:
    cur.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def popularity(cur, days: int, person: str = "", service: str = "", limit: int = 20, min_viewers: int = 2) -> list[dict]:
    """콘텐츠별 인기도 - 대시보드처럼 두 명 이상 본 것만(공개 페이지도 같은 기준)"""
    w, args = _where(days, person, service)
    return _rows(cur, f"""SELECT {CONTENT} AS content, COUNT(*) AS plays, COUNT(DISTINCT person_id) AS viewers,
        ROUND(SUM(duration_sec) / 3600.0, 1)::float AS hours
        FROM v_play_history WHERE {w} GROUP BY 1 HAVING COUNT(DISTINCT person_id) >= %s
        ORDER BY plays DESC LIMIT %s""", args + [min_viewers, limit])


def summary(cur, days: int, person: str = "", service: str = "", private: bool = True) -> dict:
    """대시보드 패널들. private=False면 사용자별·최근 재생 기록·사용자 목록을 아예 계산하지 않는다"""
    w, args = _where(days, person, service)
    wa, aargs = _where(days)            # 활성 사용자·서비스 비율·요일×시간은 대시보드에서도 필터 없이 전체
    out = {}
    cur.execute(f"SELECT COUNT(*), ROUND(COALESCE(SUM(watched_sec), 0) / 3600.0, 1)::float FROM v_play_history WHERE {w}", args)
    out["plays"], out["hours"] = cur.fetchone()
    cur.execute(f"SELECT COUNT(DISTINCT person_id) FROM v_play_history WHERE {wa}", aargs)
    out["active_users"] = cur.fetchone()[0]
    out["services"] = _rows(cur, f"SELECT source, COUNT(*) AS plays FROM v_play_history WHERE {wa} GROUP BY 1 ORDER BY 2 DESC", aargs)
    out["popular"] = popularity(cur, days, person, service)
    out["daily"] = _rows(cur, f"""SELECT {KST_DAY}::text AS day, COUNT(*) AS plays FROM v_play_history
        WHERE {_where(days, person)[0]} GROUP BY 1 ORDER BY 1""", _where(days, person)[1])
    cur.execute(f"""WITH wk AS (SELECT GREATEST(1, EXTRACT(DAYS FROM (MAX(played_at) - MIN(played_at))) / 7.0) AS weeks
        FROM v_play_history WHERE {wa})
        SELECT EXTRACT(DOW FROM played_at AT TIME ZONE 'Asia/Seoul')::int, EXTRACT(HOUR FROM played_at AT TIME ZONE 'Asia/Seoul')::int,
        ROUND(COUNT(*) / (SELECT weeks FROM wk), 1)::float FROM v_play_history WHERE {wa} GROUP BY 1, 2""", aargs + aargs)
    heat = [[0.0] * 24 for _ in range(7)]
    for dow, hour, v in cur.fetchall():
        heat[dow][hour] = v
    out["heatmap"] = heat                       # [요일 0=일][시] 주당 평균 재생 수
    out["genres"] = _rows(cur, f"""SELECT COALESCE(genre, '미분류') AS genre, COUNT(*) AS plays FROM v_play_history
        WHERE {w} AND genre IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", args)
    out["types"] = _rows(cur, f"SELECT media_type AS type, COUNT(*) AS plays FROM v_play_history WHERE {w} GROUP BY 1 ORDER BY 2 DESC", args)
    if private:
        out["users"] = _rows(cur, f"""SELECT person_name AS person, source, ROUND(SUM(watched_sec) / 3600.0, 1)::float AS hours
            FROM v_play_history WHERE {wa} GROUP BY 1, 2 ORDER BY SUM(watched_sec) DESC LIMIT 20""", aargs)
        wg, gargs = _where(days, person, service)
        out["recent"] = _rows(cur, f"""SELECT to_char(played_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS at, person_name AS person,
            CASE media_type WHEN 'movie' THEN '영화' WHEN 'episode' THEN 'TV' ELSE '기타' END AS type,
            CASE media_type WHEN 'movie' THEN title ELSE COALESCE(series_name, title) ||
                CASE WHEN season IS NOT NULL THEN ' 시즌' || season ELSE '' END END AS title,
            CASE media_type WHEN 'movie' THEN NULL ELSE CASE WHEN episode IS NOT NULL THEN episode || '화 ' ELSE '' END || title END AS episode,
            source, ROUND(watched_sec / 60.0)::int AS minutes, region_label AS region
            FROM v_play_history_geo WHERE {wg} ORDER BY played_at DESC LIMIT 300""", gargs)
        cur.execute("SELECT name FROM persons ORDER BY name")
        out["persons"] = [r[0] for r in cur.fetchall()]
    return out
