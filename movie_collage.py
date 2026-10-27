"""영화 베스트 포스터 모음 사진 (텔레그램, 2026-10-27)

포스터를 5칸 격자로 붙이고 왼쪽 위에 번호(일간 주황, 주간 파랑)를 단다. 번호는 사진 설명의 목록·
⬇번호 버튼과 같다. 글자는 숫자만 그려서 한글 폰트가 필요 없다(Pillow 기본 폰트).
"""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image, ImageDraw, ImageFont

COLS, W, H, GAP, PAD = 5, 180, 270, 10, 14
BG = (24, 26, 32)
DAY, WEEK = (235, 110, 40), (60, 120, 230)


def _poster(url: str | None) -> Image.Image | None:
    if not url:
        return None
    try:
        r = requests.get(url.replace("/w342/", "/w185/"), timeout=15)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB").resize((W, H))
    except Exception:
        return None


def make(items: list[dict], fetch=_poster) -> bytes:
    """items: enrich된 영화(poster, kind='day'|'week') → JPEG 바이트"""
    rows = max(1, (len(items) + COLS - 1) // COLS)
    canvas = Image.new("RGB", (PAD * 2 + COLS * W + (COLS - 1) * GAP, PAD * 2 + rows * H + (rows - 1) * GAP), BG)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=30)
    with ThreadPoolExecutor(max_workers=6) as ex:
        posters = list(ex.map(lambda m: fetch(m.get("poster")), items))
    for i, (m, img) in enumerate(zip(items, posters)):
        x = PAD + (i % COLS) * (W + GAP)
        y = PAD + (i // COLS) * (H + GAP)
        if img:
            canvas.paste(img, (x, y))
        else:
            draw.rectangle([x, y, x + W, y + H], fill=(60, 62, 72))
        draw.rounded_rectangle([x + 6, y + 6, x + 52, y + 46], radius=10, fill=DAY if m.get("kind") == "day" else WEEK)
        n = str(i + 1)
        draw.text((x + 29 - draw.textlength(n, font=font) / 2, y + 9), n, fill="white", font=font)
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=85)
    return buf.getvalue()
