# -*- coding: utf-8 -*-
"""핫딜 텔레그램 로그인(예비, 터미널). 보통은 MR 핫딜 > 채널 관리 화면에서 로그인한다.

  sudo /usr/local/bin/docker exec -it media_router python hotdeal_login.py          # 로그인(전화번호·인증코드·2단계 비번)
  sudo /usr/local/bin/docker exec -it media_router python hotdeal_login.py --list   # 내가 구독 중인 채널 목록
세션은 /app/data/hotdeal/tg.session(600)에 저장된다. 채널 등록은 MR 화면에서.
"""
import asyncio
import os
import sys
from pathlib import Path

from hotdeal_tg import TG

DATA = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent / "data"))) / "hotdeal"


def main():
    tg = TG(DATA)
    aid, ah = tg.api()
    if not aid:
        print("TG_API_ID / TG_API_HASH가 없어요. .env에 넣거나 MR 핫딜 > 채널 관리에서 먼저 넣어 주세요.")
        return 1
    if "--list" in sys.argv:
        for d in tg.dialogs():
            print("%-16s %-24s %s" % (d["chat_id"], ("@" + d["username"]) if d["username"] else "", d["title"]))
        return 0

    async def go():
        c = tg._client()
        await c.start()                    # 전화번호·코드·비번을 물어봄
        me = await c.get_me()
        print("로그인됨:", me.first_name or "", ("@" + me.username) if me.username else "")
        await c.disconnect()
    asyncio.run(go())
    tg._secure()
    return 0


if __name__ == "__main__":
    sys.exit(main())
