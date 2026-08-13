import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


API_URL = "https://codex-reset.com/api/timeline"
STATE_FILE = "state.json"


def http_get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "codex-reset-wechat-monitor/1.0"
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def send_serverchan(title, desp):
    sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()

    if not sendkey:
        raise RuntimeError("没有配置 SERVERCHAN_SENDKEY")

    # Server酱 Turbo
    if sendkey.startswith("SCT"):
        url = f"https://sctapi.ftqq.com/{sendkey}.send"

    # Server酱³
    elif sendkey.startswith("sctp"):
        import re

        match = re.match(r"sctp(\d+)t", sendkey)

        if not match:
            raise RuntimeError("无法识别 Server酱³ SendKey")

        server_number = match.group(1)
        url = (
            f"https://{server_number}.push.ft07.com/"
            f"send/{sendkey}.send"
        )

    else:
        raise RuntimeError("无法识别 Server酱 SendKey")

    data = urllib.parse.urlencode(
        {
            "title": title,
            "desp": desp,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": "codex-reset-wechat-monitor/1.0"
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")

    try:
        result = json.loads(body)
    except Exception:
        raise RuntimeError(
            f"Server酱返回内容无法解析: {body[:200]}"
        )

    if result.get("code") not in (0, None):
        raise RuntimeError(
            f"Server酱推送失败: {result}"
        )

    data_result = result.get("data")

    if isinstance(data_result, dict):
        errno = data_result.get("errno")

        if errno not in (0, "0", None):
            raise RuntimeError(
                f"Server酱推送失败: {result}"
            )

    print("Server酱推送请求成功")


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"seen_ids": []}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            state = json.load(file)

        if not isinstance(state.get("seen_ids"), list):
            state["seen_ids"] = []

        return state

    except Exception:
        return {"seen_ids": []}


def save_state(state):
    # 最多保存最近 100 个事件 ID
    state["seen_ids"] = state["seen_ids"][-100:]

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2,
        )


def is_reset_event(event):
    """
    只接受 Codex Reset 已经放入可信历史记录的高置信度事件。
    排除纯 banked reset / credits。
    """

    if event.get("confidence") != "high":
        return False

    if event.get("source") != "archive":
        return False

    if event.get("type") == "credits":
        return False

    summary = (
        event.get("summary")
        or ""
    ).lower()

    if event.get("group") == "reset":
        return True

    if "reset" in summary:
        return True

    return False


def format_time(value):
    if not value:
        return "未知"

    try:
        value = value.replace("Z", "+00:00")

        dt = datetime.fromisoformat(value)

        local = dt.astimezone(
            ZoneInfo("Asia/Shanghai")
        )

        return local.strftime(
            "%Y-%m-%d %H:%M:%S 北京时间"
        )

    except Exception:
        return value


def main():
    test_push = (
        os.environ.get("TEST_PUSH", "0") == "1"
    )

    # 手动测试微信推送
    if test_push:
        send_serverchan(
            "✅ Codex Reset 微信提醒测试成功",
            """
## 配置成功

GitHub Actions 已经可以通过 Server酱给你的微信发送通知。

以后检测到新的 Codex Reset 时，会自动提醒你。

**电脑不用一直开着。**
""",
        )

        print("测试推送完成")
        return

    print("正在读取 Codex Reset API")

    data = http_get_json(API_URL)

    events = data.get("events", [])

    confirmed_events = [
        event
        for event in events
        if is_reset_event(event)
    ]

    confirmed_events.sort(
        key=lambda item: item.get(
            "announced_at",
            "",
        )
    )

    if not confirmed_events:
        print("目前没有找到可信 Reset 事件")
        return

    state = load_state()

    seen_ids = set(
        str(item)
        for item in state.get(
            "seen_ids",
            [],
        )
    )

    # 第一次运行：
    # 只把现有历史记录记下来，
    # 不发送几十条旧 Reset。
    if not seen_ids:
        state["seen_ids"] = [
            str(event.get("id"))
            for event in confirmed_events
            if event.get("id")
        ]

        save_state(state)

        print(
            "首次初始化完成，"
            "历史 Reset 不发送通知。"
        )

        return

    new_events = [
        event
        for event in confirmed_events
        if str(event.get("id")) not in seen_ids
    ]

    if not new_events:
        print("没有新的 Codex Reset")
        return

    for event in new_events:
        event_id = str(event.get("id"))
        summary = (
            event.get("summary")
            or "无说明"
        )

        source_url = (
            event.get("url")
            or "https://codex-reset.com/timeline"
        )

        announced_at = format_time(
            event.get("announced_at")
        )

        preview = event.get("preview", False)

        if preview:
            status_text = (
                "Tibo 已发布可信的 Reset 公告，"
                "额度可能正在传播中。"
            )
        else:
            status_text = (
                "检测到新的高置信度 "
                "Codex Reset 记录。"
            )

        desp = f"""
## 🚨 Codex Reset

{status_text}

**时间：**

{announced_at}

**内容：**

{summary}

**原始来源：**

[点击查看原帖]({source_url})

**Codex Reset 时间线：**

[点击查看](https://codex-reset.com/timeline)

---

由 GitHub Actions 自动监控。
"""

        # 只有微信推送成功，才标记为已通知
        send_serverchan(
            "🚨 Codex 重置提醒",
            desp,
        )

        state["seen_ids"].append(event_id)

        save_state(state)

        print(
            f"已通知事件: {event_id}"
        )


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(
            f"运行失败: {error}",
            file=sys.stderr,
        )

        sys.exit(1)
