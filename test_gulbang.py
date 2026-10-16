# -*- coding: utf-8 -*-
"""gulbang.py(결방 자동 확인) 테스트.

픽스처는 2026-10-16에 실제로 받은 네이버 검색 결과(정보 박스 부분만 잘라 gzip)다.
그때 런닝맨·놀면 뭐하니는 아시안게임 중계로 9월에 2~3주 결방했었다.
"""
import gzip
from datetime import date
from pathlib import Path

import pytest

import gulbang as gb

FIX = Path(__file__).resolve().parent / "tests_fixtures"


def page(name):
    with gzip.open(FIX / f"naver_{name}.html.gz", "rt", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("subfolder,expected", [
    ("닥터X 하얀 마피아의 시대 (2026)", "닥터X 하얀 마피아의 시대"),
    ("04.시리즈/명탐정 코난 (1996)", "명탐정 코난"),
    ("04.시리즈/원피스/One Piece Season 23", "원피스"),
    ("유미의 세포들/Season03", "유미의 세포들"),
    ("런닝맨", "런닝맨"),
    ("", ""),
])
def test_show_name(subfolder, expected):
    assert gb.show_name(subfolder) == expected


def test_parse_runningman_episodes():
    box = gb.parse_info_box(page("runningman"))
    assert box["has_box"]
    dates = box["dates"]
    # 아시안게임 결방 구간(09.20, 09.27)은 비어 있고, 앞뒤 방송일은 있다
    assert "2026-09-13" in dates and "2026-10-04" in dates
    assert "2026-09-20" not in dates and "2026-09-27" not in dates
    # 회차 번호가 날짜와 같이 잡힌다 (821회 10.04 → 822회 10.11)
    eps = {e["date"]: e["no"] for e in box["episodes"]}
    assert eps["2026-10-04"] + 1 == eps["2026-10-11"]


def test_parse_no_box():
    box = gb.parse_info_box(page("nobox"))
    assert box["dates"] == []


def test_judge_gulbang_runningman():
    dates = gb.parse_info_box(page("runningman"))["dates"]
    res = gb.judge(date(2026, 9, 27), dates, today=date(2026, 9, 28))
    assert res["status"] == "gulbang"
    assert res["prev"] == "2026-09-13" and res["next"] == "2026-10-04"


def test_judge_gulbang_hangout_three_weeks():
    dates = gb.parse_info_box(page("hangout"))["dates"]
    for d in (date(2026, 9, 19), date(2026, 9, 26), date(2026, 10, 3)):
        assert gb.judge(d, dates, today=date(2026, 10, 16))["status"] == "gulbang"
    assert gb.judge(date(2026, 10, 10), dates, today=date(2026, 10, 16))["status"] == "aired"


def test_judge_scheduled_today_and_unknown_when_not_updated():
    # 나 혼자 산다: 목록이 10.09까지만 있고 오늘(10.16 금) 방송분은 아직 없음 → 결방이라고 단정하지 않음
    dates = gb.parse_info_box(page("iliveal"))["dates"]
    res = gb.judge(date(2026, 10, 16), dates, today=date(2026, 10, 16))
    assert res["status"] == "unknown"
    # 런닝맨: 다음 방송 예정일(10.18)이 목록에 있으면 그날은 '방송 예정'
    rm = gb.parse_info_box(page("runningman"))["dates"]
    assert gb.judge(date(2026, 10, 18), rm, today=date(2026, 10, 18))["status"] == "scheduled"


def test_judge_late_night_day_offset():
    # 규칙 요일이 방송일 다음날로 잡힌 경우(밤늦게 방송) 전날 방송분을 같은 회차로 본다
    dates = ["2026-10-04", "2026-10-11", "2026-10-18"]
    assert gb.judge(date(2026, 10, 12), dates, today=date(2026, 10, 16))["status"] == "aired"


def test_judge_needs_two_dates():
    assert gb.judge(date(2026, 10, 10), ["2026-10-03"], today=date(2026, 10, 16))["status"] == "unknown"


def test_expected_date():
    fri = date(2026, 10, 16)
    assert gb.expected_date("금", fri) == fri
    assert gb.expected_date("목", fri) == date(2026, 10, 15)
    assert gb.expected_date("수", fri) == date(2026, 10, 14)
    assert gb.expected_date("토", fri) == date(2026, 10, 10)


def test_check_items_end_to_end_with_news(tmp_path):
    pages = {"런닝맨": page("runningman"), "불꽃야구": page("nobox")}

    def fake_fetch(url, timeout=10.0):
        if "where=news" in url:
            return page("news_runningman")
        for name, body in pages.items():
            if gb.urllib.parse.quote(name) in url:
                return body
        raise OSError("unexpected url " + url)

    gb._mem.clear()
    items = [
        {"rkday": "rm|일", "subfolder": "런닝맨", "day": "일"},
        {"rkday": "bb|월", "subfolder": "불꽃야구", "day": "월"},
    ]
    out = gb.check_items(items, today=date(2026, 9, 28), cache_file=tmp_path / "c.json", fetch=fake_fetch)
    assert out["rm|일"]["status"] == "gulbang"
    assert out["rm|일"]["next_no"] == 821            # 결방 뒤 첫 회차
    assert "결방" in out["rm|일"].get("news", "")     # 뉴스 문장으로 이유 표시
    assert out["bb|월"]["status"] == "unknown"
    # 캐시가 파일에 남고, 두 번째 호출은 네트워크 없이 같은 결과
    gb._mem.clear()
    out2 = gb.check_items(items, today=date(2026, 9, 28), cache_file=tmp_path / "c.json",
                          fetch=lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert out2["rm|일"]["status"] == "gulbang"


def test_fetch_failure_is_unknown_not_gulbang(tmp_path):
    gb._mem.clear()

    def boom(url, timeout=10.0):
        raise OSError("down")

    out = gb.check_items([{"rkday": "x|일", "subfolder": "런닝맨", "day": "일"}],
                         today=date(2026, 9, 28), cache_file=tmp_path / "c.json", fetch=boom)
    assert out["x|일"]["status"] == "unknown"
