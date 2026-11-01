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
        out["series"] = series_progress(cur, days, person, service)
    out["playback"] = playback(cur, days, person, service, private)
    out["completion"] = completion(cur, days, person, service)
    return out


# ── 2026-11-01 추가: 재생 방식·변환, 완주율, 시리즈 진도 ──────────────────────────────
# stream_session_log는 5분마다 재생 중인 세션을 한 줄씩 남긴다(10-28부터). 한 줄 = 약 5분 시청으로 셈.
# 완주율은 같은 날 같은 사람·같은 회차를 한 번의 시청으로 묶어 '가장 멀리 간 위치 / 전체 길이'로 잰다(11-01부터 기록).
# play_history는 Plex가 다 본 것만 남기고 Jellyfin은 길이 칸에 본 시간을 넣어서 완주율 계산에 못 쓴다.
SAMPLE_MIN = 5
FINISH_PCT = 85      # 5분 간격이라 마지막 몇 분·엔딩 크레딧은 못 잡는다 → 85% 넘으면 끝까지 본 걸로
CONTENT_OF_ITEM = "regexp_replace(s.item_name, ' S[0-9]+E[0-9]+$', '')"
TRANSCODED = "(s.play_method = 'Transcode' OR s.video_decision = 'transcode')"


def _swhere(days: int, person: str = "", service: str = "") -> tuple[str, list]:
    """stream_session_log s 용 조건. 사람은 service_accounts로 이어 붙인 persons 이름"""
    sql, args = "s.seen_at >= now() - make_interval(days => %s)", [days]
    if person:
        sql += (" AND EXISTS (SELECT 1 FROM service_accounts sa JOIN persons p ON p.id = sa.person_id"
                " WHERE sa.service = s.service AND sa.username = s.username AND p.name = %s)")
        args.append(person)
    if service:
        sql += " AND s.service = %s"
        args.append(service)
    return sql, args


def playback(cur, days: int, person: str = "", service: str = "", private: bool = True) -> dict:
    """원본 그대로 재생 vs 변환 재생. 시간 = 기록 줄 수 × 5분"""
    w, args = _swhere(days, person, service)
    h = f"ROUND(COUNT(*) * {SAMPLE_MIN} / 60.0, 1)::float"
    tx_pct = f"ROUND(100.0 * COUNT(*) FILTER (WHERE {TRANSCODED}) / COUNT(*))::int"
    out = {"methods": _rows(cur, f"""SELECT COALESCE(s.play_method, '알 수 없음') AS method, {h} AS hours
        FROM stream_session_log s WHERE {w} GROUP BY 1 ORDER BY COUNT(*) DESC""", args)}
    cur.execute("SELECT to_char(MIN(seen_at) AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD') FROM stream_session_log")
    out["since"] = (cur.fetchone() or [None])[0]
    # 변환 이유: Jellyfin은 이유를 주고, Plex는 안 줘서 원본 코덱·해상도로 대신한다
    out["reasons"] = _rows(cur, f"""SELECT reason, {h} AS hours FROM (
          SELECT unnest(string_to_array(s.transcode_reasons, ',')) AS reason FROM stream_session_log s
            WHERE {w} AND {TRANSCODED} AND s.transcode_reasons IS NOT NULL
          UNION ALL
          SELECT 'plex:' || COALESCE(s.src_video_codec, '?') || ' ' || COALESCE(s.src_resolution, '') FROM stream_session_log s
            WHERE {w} AND {TRANSCODED} AND s.transcode_reasons IS NULL) x
        GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 10""", args + args)
    out["platforms"] = _rows(cur, f"""SELECT COALESCE(s.platform, '알 수 없음') AS platform, {h} AS hours, {tx_pct} AS tx_pct
        FROM stream_session_log s WHERE {w} GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 12""", args)
    out["codecs"] = _rows(cur, f"""SELECT COALESCE(s.src_video_codec, '?') AS codec, {h} AS hours, {tx_pct} AS tx_pct
        FROM stream_session_log s WHERE {w} GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 8""", args)
    if private:     # 기기 이름엔 사람 이름이 들어가기도 해서 관리자만
        out["devices"] = _rows(cur, f"""SELECT COALESCE(p.name, s.username) AS person, s.device, {h} AS hours, {tx_pct} AS tx_pct
            FROM stream_session_log s
            LEFT JOIN service_accounts sa ON sa.service = s.service AND sa.username = s.username
            LEFT JOIN persons p ON p.id = sa.person_id
            WHERE {w} GROUP BY 1, 2 HAVING COUNT(*) FILTER (WHERE {TRANSCODED}) > 0
            ORDER BY COUNT(*) FILTER (WHERE {TRANSCODED}) DESC LIMIT 15""", args)
    return out


def completion(cur, days: int, person: str = "", service: str = "", min_views: int = 2) -> dict:
    """끝까지 본 비율. 지금 보고 있는(10분 안에 기록된) 시청은 빼고 센다"""
    w, args = _swhere(days, person, service)
    views = f"""WITH v AS (
        SELECT {CONTENT_OF_ITEM} AS content,
               LEAST(100, 100.0 * MAX(s.position_sec) / NULLIF(MAX(s.duration_sec), 0)) AS pct
        FROM stream_session_log s
        WHERE {w} AND s.duration_sec > 0 AND s.position_sec IS NOT NULL
        GROUP BY s.service, s.username, s.item_name, DATE(s.seen_at AT TIME ZONE 'Asia/Seoul')
        HAVING MAX(s.seen_at) < now() - interval '10 minutes')"""
    cur.execute(f"""{views} SELECT COUNT(*), COUNT(*) FILTER (WHERE pct >= {FINISH_PCT}),
        COUNT(*) FILTER (WHERE pct < 50), ROUND(AVG(pct))::int FROM v""", args)
    n, done, early, avg = cur.fetchone() or (0, 0, 0, None)
    out = {"views": n, "finished": done, "early": early, "avg_pct": avg, "finish_pct": FINISH_PCT}
    per = f"""{views} SELECT content, COUNT(*) AS views, ROUND(AVG(pct))::int AS avg_pct,
        ROUND(100.0 * COUNT(*) FILTER (WHERE pct >= {FINISH_PCT}) / COUNT(*))::int AS done_pct
        FROM v GROUP BY 1 HAVING COUNT(*) >= %s"""
    out["best"] = _rows(cur, per + " ORDER BY avg_pct DESC, views DESC LIMIT 10", args + [min_views])
    out["worst"] = _rows(cur, per + " ORDER BY avg_pct ASC, views DESC LIMIT 10", args + [min_views])
    return out


def series_progress(cur, days: int, person: str = "", service: str = "", limit: int = 40) -> list[dict]:
    """사람별, 기간 안에 본 시리즈의 진도(관리자 전용). 본 화수는 전체 기록 기준, 전체 화수는 라이브러리 기준"""
    cond, args = "media_type = 'episode' AND series_name IS NOT NULL AND person_id IS NOT NULL", []
    if person:
        cond += " AND person_name = %s"
        args.append(person)
    if service:
        cond += " AND source = %s"
        args.append(service)
    return _rows(cur, f"""WITH g AS (
          SELECT person_name, series_name, COUNT(DISTINCT (season, episode)) AS watched, MAX(played_at) AS last_at,
                 (ARRAY_AGG(season ORDER BY played_at DESC))[1] AS season, (ARRAY_AGG(episode ORDER BY played_at DESC))[1] AS episode
          FROM v_play_history WHERE {cond} GROUP BY 1, 2),
        lib AS (SELECT series_name, MAX(n) AS total FROM (      -- library_items엔 회차 번호가 없어 서비스별 개수 중 큰 쪽
                  SELECT source, series_name, COUNT(*) AS n FROM library_items WHERE media_type = 'episode' GROUP BY 1, 2) x
                GROUP BY 1)
        SELECT g.person_name AS person, g.series_name AS series, g.watched, lib.total, g.season, g.episode,
               to_char(g.last_at AT TIME ZONE 'Asia/Seoul', 'MM-DD') AS last
        FROM g LEFT JOIN lib ON lib.series_name = g.series_name
        WHERE g.last_at >= now() - make_interval(days => %s)
        ORDER BY g.last_at DESC LIMIT %s""", args + [days, limit])
