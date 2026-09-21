import json
import os
import re
import sys
import urllib.parse
import urllib.request
import time

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


API_URL = "https://codex-reset.com/api/timeline"
STATE_FILE = "state.json"

# 只检查最近 24 小时的事件
MAX_EVENT_AGE_HOURS = 24

# 20 分钟内连续出现的 Reset，认为是同一次
DEDUP_WINDOW_MINUTES = 20


def http_get_json(url):
    last_error = None

    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent":
                        "codex-reset-serverchan-monitor/2.0"
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=20,
            ) as response:
                return json.loads(
                    response.read().decode("utf-8")
                )

        except Exception as error:
            last_error = error

            print(
                f"Codex Reset API 请求失败 "
                f"({attempt}/3): {error}"
            )

            if attempt < 3:
                wait_seconds = attempt * 5

                print(
                    f"{wait_seconds} 秒后重试..."
                )

                time.sleep(wait_seconds)

    raise RuntimeError(
        "Codex Reset API 连续请求失败 3 次: "
        f"{last_error}"
    )
def send_serverchan(title, desp):
    sendkey = os.environ.get(
        "SERVERCHAN_SENDKEY",
        "",
    ).strip()

    if not sendkey:
        raise RuntimeError(
            "没有配置 SERVERCHAN_SENDKEY"
        )

    # Server酱 Turbo
    if sendkey.startswith("SCT"):
        url = (
            f"https://sctapi.ftqq.com/"
            f"{sendkey}.send"
        )

    # Server酱³
    elif sendkey.startswith("sctp"):
        match = re.match(
            r"sctp(\d+)t",
            sendkey,
        )

        if not match:
            raise RuntimeError(
                "无法识别 Server酱³ SendKey"
            )

        server_number = match.group(1)

        url = (
            f"https://{server_number}.push.ft07.com/"
            f"send/{sendkey}.send"
        )

    else:
        raise RuntimeError(
            "无法识别 Server酱 SendKey"
        )

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
            "User-Agent":
                "codex-reset-serverchan-monitor/2.0"
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=20,
    ) as response:
        body = response.read().decode(
            "utf-8"
        )

    try:
        result = json.loads(body)

    except Exception:
        raise RuntimeError(
            "Server酱返回内容无法解析: "
            + body[:200]
        )

    if result.get("code") not in (
        0,
        None,
    ):
        raise RuntimeError(
            f"Server酱推送失败: {result}"
        )

    data_result = result.get("data")

    if isinstance(
        data_result,
        dict,
    ):
        errno = data_result.get(
            "errno"
        )

        if errno not in (
            0,
            "0",
            None,
        ):
            raise RuntimeError(
                f"Server酱推送失败: {result}"
            )

    print(
        "Server酱 App 推送请求成功"
    )


def load_state():
    if not os.path.exists(
        STATE_FILE
    ):
        return {
            "seen_ids": [],
            "last_alert_at": None,
        }

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            state = json.load(file)

    except Exception:
        return {
            "seen_ids": [],
            "last_alert_at": None,
        }

    if not isinstance(
        state.get("seen_ids"),
        list,
    ):
        state["seen_ids"] = []

    if "last_alert_at" not in state:
        state["last_alert_at"] = None

    return state


def save_state(state):
    # 最多保存 200 个事件
    state["seen_ids"] = (
        state.get(
            "seen_ids",
            [],
        )[-200:]
    )

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


def parse_time(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        ).astimezone(
            timezone.utc
        )

    except Exception:
        return None


def format_time(value):
    dt = parse_time(value)

    if not dt:
        return value or "未知"

    return dt.astimezone(
        ZoneInfo("Asia/Shanghai")
    ).strftime(
        "%Y-%m-%d %H:%M:%S 北京时间"
    )


def is_recent(event):
    announced_at = parse_time(
        event.get("announced_at")
    )

    if not announced_at:
        return False

    age = (
        datetime.now(timezone.utc)
        - announced_at
    )

    return (
        timedelta(0)
        <= age
        <= timedelta(
            hours=MAX_EVENT_AGE_HOURS
        )
    )


def has_explicit_reset_text(
    summary,
):
    """
    判断原文是不是明确表示
    Reset 已经发生或正在执行。
    """

    text = re.sub(
        r"\s+",
        " ",
        summary.lower(),
    ).strip()

    patterns = [
        r"\bhave now reset\b",
        r"\bwe have reset\b",
        r"\bwe['’]ve reset\b",
        r"\bhas been reset\b",
        r"\bhave been reset\b",
        r"\busage limits? reset\b",
        r"\breset usage limits?\b",
        r"\breset usage for all\b",
        r"\breset for all paid\b",
        r"\bwe are resetting usage\b",
        r"\bwe're resetting usage\b",
        r"\bit is done\b",
        r"\bbutton was already pressed\b",
    ]

    return any(
        re.search(
            pattern,
            text,
        )
        for pattern in patterns
    )


def looks_like_future_only(
    summary,
):
    """
    判断是否只是预告，
    而不是已经 Reset。
    """

    text = re.sub(
        r"\s+",
        " ",
        summary.lower(),
    ).strip()

    # 如果明确说已经 Reset，
    # 即使同时出现 future 单词也优先通知
    if has_explicit_reset_text(
        text
    ):
        return False

    markers = [
        "will reset",
        "going to reset",
        "will land",
        "landing tomorrow",
        "reset tomorrow",
        "tomorrow",
        "later today",
        "later in the day",
        "will come",
        "moved to tomorrow",
        "soon, but not today",
        "arriving by",
    ]

    return any(
        marker in text
        for marker in markers
    )


def is_actionable_reset_event(
    event,
):
    """
    判断这个事件是否应该
    立即推送到 Server酱 App。
    """

    if event.get("type") != "reset":
        return False

    if event.get("group") != "reset":
        return False

    scope = event.get("scope")

    if scope not in (
        None,
        "global",
    ):
        return False

    if not is_recent(event):
        return False

    summary = (
        event.get("summary")
        or ""
    ).strip()

    if not summary:
        return False

    # 只是预告，不通知“已重置”
    if looks_like_future_only(
        summary
    ):
        return False

    source = event.get("source")
    confidence = event.get(
        "confidence"
    )

    # 关键修改：
    #
    # live + medium 也可以通知，
    # 但必须原文明确表示已经 Reset
    if source == "live":
        return has_explicit_reset_text(
            summary
        )

    # 原来的 archive + high
    # 继续支持
    if (
        source == "archive"
        and confidence == "high"
    ):
        if event.get("preview"):
            return (
                has_explicit_reset_text(
                    summary
                )
            )

        return True

    return False


def cluster_events(events):
    """
    把短时间内关于同一次 Reset
    的多条帖子合并成一组。
    """

    ordered = sorted(
        events,
        key=lambda item:
            parse_time(
                item.get(
                    "announced_at"
                )
            )
            or datetime.min.replace(
                tzinfo=timezone.utc
            ),
    )

    clusters = []

    for event in ordered:
        event_time = parse_time(
            event.get(
                "announced_at"
            )
        )

        if not event_time:
            continue

        if not clusters:
            clusters.append(
                [event]
            )
            continue

        last_event = (
            clusters[-1][-1]
        )

        last_time = parse_time(
            last_event.get(
                "announced_at"
            )
        )

        if (
            last_time
            and event_time
            - last_time
            <= timedelta(
                minutes=
                DEDUP_WINDOW_MINUTES
            )
        ):
            clusters[-1].append(
                event
            )

        else:
            clusters.append(
                [event]
            )

    return clusters


def main():
    test_push = (
        os.environ.get(
            "TEST_PUSH",
            "0",
        )
        == "1"
    )

    # 手动测试 Server酱 App
    if test_push:
        send_serverchan(
            "✅ Codex Reset 提醒测试成功",
            """
## Server酱 App 推送测试成功

GitHub Actions 已经可以通过 Server酱发送通知。

以后检测到 Codex 明确已经 Reset 时，会自动推送到 Server酱 App。

电脑不需要一直开着。
""",
        )

        print(
            "Server酱 App 测试推送完成"
        )

        return

    print(
        "正在读取 Codex Reset API"
    )

    data = http_get_json(
        API_URL
    )

    events = data.get(
        "events",
        [],
    )

    state = load_state()

    seen_ids = {
        str(item)
        for item in state.get(
            "seen_ids",
            [],
        )
    }

    actionable_events = [
        event
        for event in events
        if is_actionable_reset_event(
            event
        )
    ]

    new_events = [
        event
        for event
        in actionable_events
        if str(
            event.get("id")
        )
        not in seen_ids
    ]

    if not new_events:
        print(
            "没有新的可通知 Codex Reset"
        )
        return

    last_alert_at = parse_time(
        state.get(
            "last_alert_at"
        )
    )

    # 同一次 Reset 的多条消息合并
    for cluster in cluster_events(
        new_events
    ):
        event = max(
            cluster,
            key=lambda item:
                parse_time(
                    item.get(
                        "announced_at"
                    )
                )
                or datetime.min.replace(
                    tzinfo=timezone.utc
                ),
        )

        event_time = parse_time(
            event.get(
                "announced_at"
            )
        )

        # 和上一次推送相差 20 分钟以内
        # 也认为是同一轮 Reset
        if (
            last_alert_at
            and event_time
            and abs(
                event_time
                - last_alert_at
            )
            <= timedelta(
                minutes=
                DEDUP_WINDOW_MINUTES
            )
        ):
            for item in cluster:
                event_id = str(
                    item.get("id")
                )

                if (
                    event_id
                    and event_id
                    not in seen_ids
                ):
                    state[
                        "seen_ids"
                    ].append(
                        event_id
                    )

                    seen_ids.add(
                        event_id
                    )

            save_state(
                state
            )

            print(
                "跳过同一次 Reset 的重复消息"
            )

            continue

        summary = (
            event.get("summary")
            or "无说明"
        )

        source_url = (
            event.get("url")
            or
            "https://codex-reset.com/timeline"
        )

        announced_at = (
            format_time(
                event.get(
                    "announced_at"
                )
            )
        )

        confidence = (
            event.get(
                "confidence"
            )
            or "未知"
        )

        source_label = (
            event.get(
                "source_label"
            )
            or event.get(
                "source"
            )
            or "未知"
        )

        desp = f"""
## 🚨 Codex 已重置

检测到 **Codex / ChatGPT Work Reset 已经发生或正在执行**。

**时间：**

{announced_at}

**内容：**

{summary}

**来源状态：**

{source_label}

**Confidence：**

{confidence}

**原始来源：**

[点击查看原帖]({source_url})

**Codex Reset 时间线：**

[点击查看](https://codex-reset.com/timeline)

---

由 GitHub Actions 自动监控。

短时间内关于同一次 Reset 的重复帖子会自动合并。
"""

        # 只有 Server酱成功后
        # 才写入状态
        send_serverchan(
            "🚨 Codex 已重置，快去用额度",
            desp,
        )

        for item in cluster:
            event_id = str(
                item.get("id")
            )

            if (
                event_id
                and event_id
                not in seen_ids
            ):
                state[
                    "seen_ids"
                ].append(
                    event_id
                )

                seen_ids.add(
                    event_id
                )

        if event_time:
            state[
                "last_alert_at"
            ] = (
                event_time.isoformat()
            )

            last_alert_at = (
                event_time
            )

        save_state(
            state
        )

        print(
            "已通过 Server酱 App "
            f"通知 Reset: "
            f"{event.get('id')}"
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
