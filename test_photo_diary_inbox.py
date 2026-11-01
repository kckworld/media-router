import json

import web_admin


def test_photo_diary_button_is_forwarded_to_inbox(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(web_admin, "PHOTO_DIARY_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(web_admin, "_tg_call", lambda m, p, t=None: calls.append((m, p)) or {"ok": True})
    # 오래된 버튼이어도 MR은 만료 판단 없이 넘기기만 한다 (판단은 photo-diary 쪽)
    web_admin._handle_telegram_callback({"id": "q1", "data": "pd_post_20261101:123",
                                         "message": {"chat": {"id": 1}, "message_id": 7}})
    files = list((tmp_path / "inbox").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == {"action": "pd_post_20261101", "ts": "123", "message_id": 7}
    assert [m for m, _ in calls] == ["answerCallbackQuery"]
