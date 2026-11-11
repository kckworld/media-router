# -*- coding: utf-8 -*-
"""메모장(11-11): 만들기·고치기(충돌)·고정·삭제(사진 정리)·사진(줄이기·EXIF 없음)·공유 링크(읽기 전용, 그 메모 사진만)·소유자만"""
import io
import json

import pytest
from PIL import Image

import accounts
import notes
import web_admin


def jpeg(w=3000, h=2000):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 100, 50)).save(b, "JPEG", exif=Image.Exif())
    return b.getvalue()


@pytest.fixture
def nt(monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "NOTES_DIR", tmp_path / "notes")
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "current_user", lambda req: accounts.OWNER)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), tmp_path / "notes"


def test_create_update_conflict_pin_order(nt):
    c, d = nt
    a = c.post("/api/notes", json={"title": "장보기", "body": "우유\n계란"}).get_json()["note"]
    b = c.post("/api/notes", json={"title": "할 일", "body": ""}).get_json()["note"]
    r = c.post(f"/api/notes/{a['id']}", json={"base": str(a["rev"]), "title": "장보기!", "body": "우유"}).get_json()
    assert r["ok"] and r["note"]["title"] == "장보기!" and r["note"]["rev"] == 2
    seen = r["note"]["rev"]
    # 같은 1초 안이라도 그 사이 다른 기기가 고쳤으면(rev가 다름) 409
    notes.update(d, a["id"], "", "다른 기기", "x")
    r = c.post(f"/api/notes/{a['id']}", json={"base": str(seen), "title": "덮어쓰기", "body": "y"})
    assert r.status_code == 409 and r.get_json()["conflict"] and r.get_json()["note"]["title"] == "다른 기기"
    assert c.post(f"/api/notes/{b['id']}", json={"pinned": True}).get_json()["note"]["pinned"]
    ids = [n["id"] for n in c.get("/api/notes").get_json()["notes"]]
    assert ids[0] == b["id"]                                              # 고정이 맨 위
    assert c.post("/api/notes", json={"title": "x", "body": "가" * (notes.BODY_MAX + 1)}).status_code == 400


def test_image_shrink_no_exif_and_delete_cleanup(nt):
    c, d = nt
    r = c.post("/api/notes/image", data={"file": (io.BytesIO(jpeg()), "a.jpg")}, content_type="multipart/form-data").get_json()
    assert r["ok"]
    p = d / "img" / r["name"]
    im = Image.open(p)
    assert max(im.size) == notes.IMG_SIDE and not im.getexif()
    assert c.post("/api/notes/image", data={"file": (io.BytesIO(b"not image"), "a.jpg")},
                  content_type="multipart/form-data").status_code == 400
    n = c.post("/api/notes", json={"title": "사진", "body": f"앞\n[[사진:{r['name']}]]\n뒤"}).get_json()["note"]
    assert c.get(f"/notes/img/{r['name']}").status_code == 200
    assert c.get("/notes/img/../notes.json").status_code == 404
    assert c.post(f"/api/notes/{n['id']}/delete", json={}).get_json()["ok"] and not p.exists()


def test_share_link_read_only(nt, monkeypatch):
    c, d = nt
    name, _ = notes.save_image(d, jpeg(400, 300))
    other, _ = notes.save_image(d, jpeg(400, 300))
    n = c.post("/api/notes", json={"title": "공유 메모", "body": f"<b>안녕</b>\n[[사진:{name}]]"}).get_json()["note"]
    notes.create(d, "비밀", f"[[사진:{other}]]")
    r = c.post(f"/api/notes/{n['id']}/share", json={"on": True}).get_json()
    url = r["note"]["share_url"]
    assert url.startswith("https://") and "/pub/n/" in url
    tok = url.rsplit("/", 1)[-1]
    pub = {"Host": "request.kckworld.com"}
    monkeypatch.setattr(web_admin, "authed", lambda req: False)              # 로그인 안 한 사람
    h = c.get(f"/pub/n/{tok}", headers=pub)
    html = h.get_data(as_text=True)
    assert h.status_code == 200 and "공유 메모" in html and "&lt;b&gt;안녕" in html and "<script" not in html
    assert c.get(f"/pub/n/{tok}/img/{name}", headers=pub).status_code == 200
    assert c.get(f"/pub/n/{tok}/img/{other}", headers=pub).status_code == 404      # 다른 메모 사진은 안 됨
    assert c.get("/pub/n/" + "x" * 22, headers=pub).status_code == 404
    assert c.get("/api/notes", headers=pub).status_code == 404                      # 공개 주소에서 관리 API 안 열림
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    c.post(f"/api/notes/{n['id']}/share", json={"on": False})
    monkeypatch.setattr(web_admin, "authed", lambda req: False)
    assert c.get(f"/pub/n/{tok}", headers=pub).status_code == 404                   # 끄면 바로 안 열림


def test_owner_only_and_pages(nt, monkeypatch):
    c, d = nt
    assert 'id="ntCards"' in c.get("/notes").get_data(as_text=True)
    app_html = c.get("/notes?app=1").get_data(as_text=True)
    assert "notes.webmanifest" in app_html and "notes-icon-180.png" in app_html
    m = json.loads(open("static/notes.webmanifest", encoding="utf-8").read())
    assert m["start_url"] == "/notes?app=1"
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")
    monkeypatch.setattr(web_admin, "_req_user_perms", lambda req: ("someone", {}))
    assert c.get("/api/notes").status_code in (302, 403)
