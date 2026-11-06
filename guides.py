# -*- coding: utf-8 -*-
"""공개 사이트 '사용법 안내' (2026-11-07): data/guide/<slug>.md 문서 + data/guide/img/ 그림.

- 편집은 MR 설정 > 공개 사이트(소유자만), 공개 쪽은 읽기만.
- 마크다운 → HTML은 여기서 직접 한다(라이브러리 없음): 글자는 전부 이스케이프한 뒤 정해진 몇 가지만 태그로.
  제목(#~###)·문단·목록(-, 1.)·인용(>)·코드(``` / `)·굵게·기울임·가로줄·링크(http(s)·/pub/만)·그림(/pub/guide/img/만).
  원본 HTML은 그대로 글자로 보임 → 스크립트·이벤트 속성·javascript: 링크가 끼어들 틈이 없음.
- 그림: PNG·JPEG·WebP·GIF(첫 장)만, 8MB·4000px 넘으면 거절, 다시 저장해 메타데이터(EXIF·GPS) 제거, 긴 변 1600px, 이름은 무작위.
"""
from __future__ import annotations

import html
import io
import os
import re
import secrets
import time
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
IMG_RE = re.compile(r"^[0-9a-f]{20}\.(?:jpg|png)$")
MAX_DOC = 100_000
MAX_UPLOAD = 8 * 1024 * 1024
MAX_PIXELS = 4000 * 4000
MAX_SIDE = 1600


def _doc_path(root: Path, slug: str) -> Path | None:
    return root / f"{slug}.md" if SLUG_RE.match(slug or "") else None


def title_of(text: str, slug: str) -> str:
    for ln in text.splitlines():
        m = re.match(r"^#\s+(.+)$", ln.strip())
        if m:
            return m.group(1).strip()[:80]
    return slug


def listing(root: Path) -> list[dict]:
    out = []
    for p in sorted(root.glob("*.md")) if root.is_dir() else []:
        slug = p.stem
        if not SLUG_RE.match(slug):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({"slug": slug, "title": title_of(text, slug), "updated": time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime)),
                    "size": len(text)})
    return out


def read(root: Path, slug: str) -> str | None:
    p = _doc_path(root, slug)
    try:
        return p.read_text(encoding="utf-8") if p else None
    except OSError:
        return None


def save(root: Path, slug: str, text: str) -> str:
    """'' 이면 성공, 아니면 오류 문장"""
    p = _doc_path(root, slug)
    if not p:
        return "문서 이름은 영어 소문자·숫자·-로 40자까지예요(예: plex-install)."
    if len(text) > MAX_DOC:
        return "문서가 너무 길어요(10만 자까지)."
    root.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
    os.replace(tmp, p)
    return ""


def delete(root: Path, slug: str) -> bool:
    """지우지 않고 .trash/로 옮김(되살릴 수 있게)"""
    p = _doc_path(root, slug)
    if not p or not p.exists():
        return False
    trash = root / ".trash"
    trash.mkdir(exist_ok=True)
    os.replace(p, trash / f"{slug}.{time.strftime('%Y%m%d%H%M%S')}.md")
    return True


def save_image(root: Path, data: bytes) -> tuple[str | None, str]:
    """(파일 이름, 오류) — 다시 저장해 메타데이터 제거"""
    if not data or len(data) > MAX_UPLOAD:
        return None, "그림은 8MB까지예요."
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        if im.format not in ("PNG", "JPEG", "WEBP", "GIF"):
            return None, "PNG·JPG·WebP·GIF만 올릴 수 있어요."
        if im.width * im.height > MAX_PIXELS:
            return None, "그림이 너무 커요(4000×4000까지)."
        im.load()
    except Exception:
        return None, "그림 파일이 아니에요."
    keep_png = im.format in ("PNG", "GIF") or im.mode in ("RGBA", "LA", "P")
    im = im.convert("RGBA" if keep_png else "RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    name = secrets.token_hex(10) + (".png" if keep_png else ".jpg")
    d = root / "img"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    if keep_png:
        im.save(tmp, "PNG", optimize=True)
    else:
        im.save(tmp, "JPEG", quality=85, optimize=True)
    os.replace(tmp, d / name)
    return name, ""


def image_path(root: Path, name: str) -> Path | None:
    if not IMG_RE.match(name or ""):
        return None
    p = root / "img" / name
    return p if p.is_file() else None


# ── 마크다운(일부) → 안전한 HTML ──
_URL_OK = re.compile(r"^(?:https?://[^\s\"'<>]+|/pub/[A-Za-z0-9/_\-.?=&;%]*)$")
_IMG_OK = re.compile(r"^/pub/guide/img/[0-9a-f]{20}\.(?:jpg|png)$")


def _inline(s: str) -> str:
    """s는 이미 이스케이프된 글자"""
    codes = []

    def keep_code(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"
    s = re.sub(r"`([^`]+)`", keep_code, s)

    def img(m):
        alt, url = m.group(1), html.unescape(m.group(2))
        return f'<img src="{html.escape(url)}" alt="{alt}" loading="lazy">' if _IMG_OK.match(url) else m.group(0)
    s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", img, s)

    def link(m):
        text, url = m.group(1), html.unescape(m.group(2))
        if not _URL_OK.match(url):
            return text
        ext = url.startswith("http")
        return f'<a href="{html.escape(url)}"' + (' rel="noopener noreferrer nofollow" target="_blank"' if ext else "") + f">{text}</a>"
    s = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)", link, s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", s)


def render(md: str) -> str:
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out, para, i = [], [], 0
    list_kind = None

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_inline(html.escape(x)) for x in para) + "</p>")
            para.clear()

    def close_list():
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None
    while i < len(lines):
        raw = lines[i]
        ln = raw.strip()
        if ln.startswith("```"):
            flush_para(); close_list()
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.+)$", ln)
        ul = re.match(r"^[-*]\s+(.+)$", ln)
        ol = re.match(r"^\d{1,3}[.)]\s+(.+)$", ln)
        if not ln:
            flush_para(); close_list()
        elif m:
            flush_para(); close_list()
            lv = len(m.group(1)) + 1                          # # → h2 (화면 제목이 h1)
            out.append(f"<h{lv}>{_inline(html.escape(m.group(2)))}</h{lv}>")
        elif re.match(r"^(-{3,}|\*{3,})$", ln):
            flush_para(); close_list()
            out.append("<hr>")
        elif ln.startswith(">"):
            flush_para(); close_list()
            out.append("<blockquote>" + _inline(html.escape(ln.lstrip("> ").strip())) + "</blockquote>")
        elif ul or ol:
            flush_para()
            kind = "ul" if ul else "ol"
            if list_kind != kind:
                close_list()
                out.append(f"<{kind}>")
                list_kind = kind
            out.append("<li>" + _inline(html.escape((ul or ol).group(1))) + "</li>")
        else:
            close_list()
            para.append(ln)
        i += 1
    flush_para(); close_list()
    return "\n".join(out)
