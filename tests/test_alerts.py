"""失败告警：连续失败到阈值才发、冷却期内不重复、恢复后清零；没配不启用。"""

import json

import httpx

from app.services.alerts import Alerter


def make():
    sent = []

    async def handler(request):
        sent.append((request.url.path, json.loads(request.read())))
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Alerter("tok", "42", client, save_fails=3, source_fails=2), sent


async def test_save_streak_dedupe_and_reset():
    alerter, sent = make()
    for _ in range(2):
        await alerter.save_result(7, "我不是大师", False, "连不上夸克")
    assert sent == []
    await alerter.save_result(7, "我不是大师", False, "连不上夸克")
    assert len(sent) == 1 and sent[0][0] == "/bottok/sendMessage"
    assert sent[0][1]["chat_id"] == "42" and "连续失败 3 次" in sent[0][1]["text"]
    await alerter.save_result(7, "我不是大师", False, "x")  # 冷却期内不重复
    assert len(sent) == 1
    await alerter.save_result(8, "繁花", True)
    await alerter.source_result("pansou", False)
    await alerter.source_result("pansou", True)  # 恢复：清零
    await alerter.source_result("pansou", False)
    assert len(sent) == 1
    await alerter.source_result("pansou", False)
    assert len(sent) == 2 and "pansou" in sent[1][1]["text"]
    await alerter.login_expired("u:uid:123456789", "繁花")
    assert "…456789" in sent[2][1]["text"] and "uid:123" not in sent[2][1]["text"]


async def test_disabled_without_config():
    alerter = Alerter("", "")
    assert not alerter.enabled
    assert await alerter.send("k", "x") is False
