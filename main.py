import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


def _default_db_path() -> str:
    env = os.getenv("ASTRBOT_DB_PATH")
    if env:
        return env
    candidates: list[Path] = []
    try:
        candidates.append(Path(__file__).resolve().parents[2] / "data_v4.db")
    except Exception:
        pass
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        candidates.append(Path(get_astrbot_data_path()) / "data_v4.db")
    except Exception:
        pass
    for path in candidates:
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    if candidates:
        return str(candidates[0])
    return os.path.join(os.path.expanduser("~"), "data_v4.db")


DB_PATH = _default_db_path()
BJ = timezone(timedelta(hours=8))
KINDS = ("private", "group", "other")

PRICES = {
    "deepseek-flash": {
        "peak": {"cache": 0.04, "miss": 2.0, "out": 8.0},
        "idle": {"cache": 0.02, "miss": 1.0, "out": 4.0},
    },
    "deepseek-v4-pro": {
        "peak": {"cache": 0.30, "miss": 9.0, "out": 27.0},
        "idle": {"cache": 0.15, "miss": 4.5, "out": 13.5},
    },
    "deepseek-chat": {
        "peak": {"cache": 0.04, "miss": 2.0, "out": 8.0},
        "idle": {"cache": 0.02, "miss": 1.0, "out": 4.0},
    },
    "deepseek-reasoner": {
        "peak": {"cache": 0.04, "miss": 4.0, "out": 16.0},
        "idle": {"cache": 0.02, "miss": 2.0, "out": 8.0},
    },
    "gpt-4o": {
        "peak": {"cache": 1.25, "miss": 2.50, "out": 10.0},
        "idle": {"cache": 1.25, "miss": 2.50, "out": 10.0},
    },
    "gpt-4o-mini": {
        "peak": {"cache": 0.075, "miss": 0.15, "out": 0.60},
        "idle": {"cache": 0.075, "miss": 0.15, "out": 0.60},
    },
    "gpt-4.1": {
        "peak": {"cache": 0.50, "miss": 2.00, "out": 8.00},
        "idle": {"cache": 0.50, "miss": 2.00, "out": 8.00},
    },
    "o3": {
        "peak": {"cache": 0.50, "miss": 2.00, "out": 8.00},
        "idle": {"cache": 0.50, "miss": 2.00, "out": 8.00},
    },
    "o4-mini": {
        "peak": {"cache": 0.275, "miss": 1.10, "out": 4.40},
        "idle": {"cache": 0.275, "miss": 1.10, "out": 4.40},
    },
    "claude-3-5-sonnet": {
        "peak": {"cache": 0.30, "miss": 3.00, "out": 15.0},
        "idle": {"cache": 0.30, "miss": 3.00, "out": 15.0},
    },
    "claude-3-7-sonnet": {
        "peak": {"cache": 0.30, "miss": 3.00, "out": 15.0},
        "idle": {"cache": 0.30, "miss": 3.00, "out": 15.0},
    },
    "claude-sonnet-4": {
        "peak": {"cache": 0.30, "miss": 3.00, "out": 15.0},
        "idle": {"cache": 0.30, "miss": 3.00, "out": 15.0},
    },
    "gemini-2.5-flash": {
        "peak": {"cache": 0.03, "miss": 0.30, "out": 2.50},
        "idle": {"cache": 0.03, "miss": 0.30, "out": 2.50},
    },
    "gemini-2.5-pro": {
        "peak": {"cache": 0.125, "miss": 1.25, "out": 10.0},
        "idle": {"cache": 0.125, "miss": 1.25, "out": 10.0},
    },
    "qwen-plus": {
        "peak": {"cache": 0.10, "miss": 0.80, "out": 2.00},
        "idle": {"cache": 0.10, "miss": 0.80, "out": 2.00},
    },
    "qwen-max": {
        "peak": {"cache": 0.30, "miss": 2.40, "out": 9.60},
        "idle": {"cache": 0.30, "miss": 2.40, "out": 9.60},
    },
    "qwen-turbo": {
        "peak": {"cache": 0.05, "miss": 0.30, "out": 0.60},
        "idle": {"cache": 0.05, "miss": 0.30, "out": 0.60},
    },
}
DEFAULT_PRICE = PRICES["deepseek-flash"]


def _is_peak(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    h = dt.hour
    return (9 <= h < 12) or (14 <= h < 18)


def _price_for(model: str, dt: datetime) -> dict:
    raw = str(model or "").strip().lower()
    key = raw.split("/")[-1]
    table = PRICES.get(key)
    if table is None:
        for name in sorted(PRICES, key=len, reverse=True):
            if name in key:
                table = PRICES[name]
                break
    table = table or DEFAULT_PRICE
    return table["peak"] if _is_peak(dt) else table["idle"]


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


PLATFORM_LABELS = {
    "aiocqhttp": "QQ",
    "snowluma": "QQ / SnowLuma",
    "qq": "QQ",
    "webchat": "WebChat",
    "telegram": "Telegram",
    "discord": "Discord",
    "slack": "Slack",
    "lark": "飞书",
    "feishu": "飞书",
    "wecom": "企业微信",
    "wechat": "微信",
    "dingtalk": "钉钉",
    "kook": "KOOK",
    "mattermost": "Mattermost",
    "matrix": "Matrix",
    "misskey": "Misskey",
}


def _parse_umo(umo: str) -> tuple[str, str, str, str, str, str]:
    parts = str(umo or "").split(":")
    platform = parts[0] if len(parts) > 0 else "unknown"
    mtype = parts[1] if len(parts) > 1 else ""
    sid = parts[2] if len(parts) > 2 else ""
    low = mtype.lower()
    if any(token in low for token in ("group", "channel", "guild", "room")):
        kind = "group"
        kind_label = "群聊"
    elif any(token in low for token in ("friend", "private", "direct", "dm", "c2c")):
        kind = "private"
        kind_label = "私聊"
    else:
        kind = "other"
        kind_label = "会话"
    platform_label = PLATFORM_LABELS.get(platform.lower(), platform or "未知平台")
    label = f"{platform_label} · {kind_label}"
    if sid:
        label += f" {sid}"
    return platform, mtype, sid, kind, label, platform_label



def _iter_rows():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    rows = cur.execute(
        "select provider_model, token_input_other, token_input_cached, token_output, start_time, created_at, umo "
        "from provider_stats where status='completed'"
    ).fetchall()
    con.close()
    for model, tin, tcached, tout, start_time, created_at, umo in rows:
        try:
            if start_time:
                dt = datetime.fromtimestamp(float(start_time), BJ)
            else:
                dt = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ)
        except Exception:
            continue
        yield model or "unknown", int(tin or 0), int(tcached or 0), int(tout or 0), dt, umo or ""


def _reply_counts() -> dict[str, int]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    result: dict[str, int] = {}
    for user_id, content in cur.execute("select user_id, content from conversations"):
        try:
            data = json.loads(content or "[]")
        except Exception:
            continue
        result[user_id] = sum(1 for m in data if isinstance(m, dict) and m.get("role") == "assistant")
    con.close()
    return result


def _collect(
    days: int = 7,
    date_str: str | None = None,
    platform_filter: str | None = None,
    model_filter: str | None = None,
) -> dict:
    """统计指定自然日或最近 N 个自然日。

    单日模式严格从当天 00:00 到次日 00:00，不做滚动 24 小时。
    platform_filter / model_filter 在时间范围基础上再筛选；facets 始终返回
    当前时间范围内全部平台和模型，供页面筛选器使用。
    """
    platform_filter = str(platform_filter or "").strip()
    model_filter = str(model_filter or "").strip()
    now = datetime.now(BJ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    selected_date = ""
    if date_str:
        try:
            selected_date = str(date_str).strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", selected_date):
                raise ValueError
            start = datetime.strptime(selected_date, "%Y-%m-%d").replace(tzinfo=BJ)
            end = start + timedelta(days=1)
        except Exception:
            selected_date = ""
    if not selected_date:
        days = max(1, min(int(days or 1), 90))
        start = today - timedelta(days=days - 1)
        end = None
        period_label = "今天" if days == 1 else f"近 {days} 天"
    else:
        days = 1
        period_label = "今天" if selected_date == today.strftime("%Y-%m-%d") else selected_date

    requests = 0
    total_in = total_cache = total_out = 0
    cost = 0.0
    peak_cost = idle_cost = 0.0
    peak_tokens = idle_tokens = 0
    by_model: dict[str, dict] = {}
    by_day: dict[str, float] = {}
    recent: list[dict] = []
    replies_map = _reply_counts()
    convs: dict[str, dict] = {}
    kinds: dict[str, dict] = {
        k: {"requests": 0, "tokens": 0, "cost": 0.0} for k in KINDS
    }
    platforms: dict[str, dict] = {}
    facet_platforms: dict[str, dict] = {}
    facet_models: dict[str, dict] = {}

    for model, tin, tcached, tout, dt, umo in _iter_rows():
        if dt < start or (end is not None and dt >= end):
            continue
        platform, mtype, sid, kind, label, platform_label = _parse_umo(umo)
        pf = facet_platforms.setdefault(platform, {
            "platform": platform,
            "label": platform_label,
            "requests": 0,
        })
        pf["requests"] += 1
        fm = facet_models.setdefault(model, {"model": model, "requests": 0})
        fm["requests"] += 1
        if platform_filter and platform != platform_filter:
            continue
        if model_filter and model != model_filter:
            continue
        p = _price_for(model, dt)
        c = (tin / 1_000_000) * p["miss"] + (tcached / 1_000_000) * p["cache"] + (tout / 1_000_000) * p["out"]
        cost += c
        requests += 1
        total_in += tin
        total_cache += tcached
        total_out += tout
        tokens = tin + tcached + tout
        if _is_peak(dt):
            peak_cost += c
            peak_tokens += tokens
        else:
            idle_cost += c
            idle_tokens += tokens
        m = by_model.setdefault(model, {"input": 0, "cache": 0, "output": 0, "cost": 0.0})
        m["input"] += tin
        m["cache"] += tcached
        m["output"] += tout
        m["cost"] += c
        day = dt.strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0.0) + c
        recent.append({
            "time": dt.strftime("%m-%d %H:%M"),
            "model": model,
            "input": tin,
            "cache": tcached,
            "output": tout,
            "cost": round(c, 4),
            "umo": umo,
            "platform": platform,
        })

        conv = convs.setdefault(umo, {
            "umo": umo,
            "platform": platform,
            "platform_label": platform_label,
            "kind": kind,
            "label": label,
            "requests": 0,
            "input": 0,
            "cache": 0,
            "output": 0,
            "tokens": 0,
            "cost": 0.0,
            "replies": replies_map.get(umo, 0),
        })
        conv["requests"] += 1
        conv["input"] += tin
        conv["cache"] += tcached
        conv["output"] += tout
        conv["tokens"] += tokens
        conv["cost"] += c

        pf = platforms.setdefault(platform, {
            "platform": platform,
            "label": platform_label,
            "requests": 0,
            "tokens": 0,
            "cost": 0.0,
        })
        pf["requests"] += 1
        pf["tokens"] += tokens
        pf["cost"] += c

        k = kinds[kind]
        k["requests"] += 1
        k["tokens"] += tokens
        k["cost"] += c

    series = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        series.append({
            "day": d[5:],
            "date": d,
            "cost": round(by_day.get(d, 0.0), 4),
        })

    recent.sort(key=lambda x: x["time"], reverse=True)
    total_tokens = total_in + total_cache + total_out

    conv_list = []
    for item in convs.values():
        item["cost"] = round(item["cost"], 4)
        item["avg_call"] = round(item["cost"] / item["requests"], 4) if item["requests"] else 0
        item["avg_reply"] = round(item["cost"] / item["replies"], 4) if item["replies"] else 0
        item["cache_rate"] = (
            round(item["cache"] / item["tokens"] * 100, 1) if item["tokens"] else 0
        )
        item["share"] = round(item["cost"] / cost * 100, 1) if cost else 0
        conv_list.append(item)
    conv_list.sort(key=lambda x: x["cost"], reverse=True)

    kinds_out = {}
    for k, v in kinds.items():
        kinds_out[k] = {
            "requests": v["requests"],
            "tokens": v["tokens"],
            "cost": round(v["cost"], 4),
            "share": round(v["cost"] / cost * 100, 1) if cost else 0,
        }
    platforms_out = []
    for p in platforms.values():
        p["cost"] = round(p["cost"], 4)
        p["share"] = round(p["cost"] / cost * 100, 1) if cost else 0
        platforms_out.append(p)
    platforms_out.sort(key=lambda x: x["cost"], reverse=True)

    facets = {
        "platforms": sorted(facet_platforms.values(), key=lambda x: -x["requests"]),
        "models": sorted(facet_models.values(), key=lambda x: -x["requests"]),
    }

    return {
        "days": days,
        "date": selected_date or None,
        "label": period_label,
        "platform_filter": platform_filter,
        "model_filter": model_filter,
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "end": end.strftime("%Y-%m-%d %H:%M") if end else "现在",
        "requests": requests,
        "input": total_in,
        "cache": total_cache,
        "output": total_out,
        "tokens": total_tokens,
        "cost": round(cost, 4),
        "avg_request": round(cost / requests, 4) if requests else 0,
        "cache_rate": round(total_cache / total_tokens * 100, 1) if total_tokens else 0,
        "peak": {"cost": round(peak_cost, 4), "tokens": peak_tokens},
        "idle": {"cost": round(idle_cost, 4), "tokens": idle_tokens},
        "models": by_model,
        "series": series,
        "recent": recent[:8],
        "conversations": conv_list,
        "kinds": kinds_out,
        "platforms": platforms_out,
        "facets": facets,
    }


def _assistant_count() -> int:
    return sum(_reply_counts().values())


def _all_time_cost() -> float:
    cost = 0.0
    for model, tin, tcached, tout, dt, _umo in _iter_rows():
        p = _price_for(model, dt)
        cost += (tin / 1_000_000) * p["miss"] + (tcached / 1_000_000) * p["cache"] + (tout / 1_000_000) * p["out"]
    return cost


def summarize(days: int = 1, date_str: str | None = None) -> str:
    s = _collect(days, date_str)
    label = s.get("label") or f"最近 {days} 天"
    if not s["models"]:
        return f"{label}没有 API 调用记录。"
    lines = [f"{label} API 用量（{s.get('start', '')} 至 {s.get('end', '')}）："]
    for model, m in sorted(s["models"].items(), key=lambda x: -x[1]["cost"]):
        lines.append(
            f"· {model}：输入 {_fmt_tokens(m['input'])}（缓存命中 {_fmt_tokens(m['cache'])}），"
            f"输出 {_fmt_tokens(m['output'])}，约 ¥{m['cost']:.4f}"
        )
    k = s["kinds"]
    lines.append(
        f"私聊 ¥{k['private']['cost']:.4f} · 群聊 ¥{k['group']['cost']:.4f}"
    )
    lines.append(
        f"合计：请求 {s['requests']} 次，输入 {_fmt_tokens(s['input'])}（缓存 {_fmt_tokens(s['cache'])}），"
        f"输出 {_fmt_tokens(s['output'])}，约 ¥{s['cost']:.4f}"
    )
    return "\n".join(lines)


class CostPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        try:
            self.context.register_web_api(
                "/astrbot_plugin_cost/summary",
                self._summary_api,
                ["GET"],
                "API 用量与费用汇总",
            )
        except Exception as exc:
            logger.warning(f"[cost] 注册 Web API 失败: {exc}")

    async def _summary_api(self):
        date_str = ""
        platform = ""
        model = ""
        try:
            from quart import request as qreq

            date_str = str(qreq.args.get("date", "") or "").strip()
            platform = str(qreq.args.get("platform", "") or "").strip()
            model = str(qreq.args.get("model", "") or "").strip()
            days = int(qreq.args.get("days", 7))
        except Exception:
            days = 7
        days = max(1, min(days, 90))
        s = _collect(days, date_str or None, platform or None, model or None)
        assistant = _assistant_count()
        total_cost = _all_time_cost()
        return {
            "ok": True,
            "days": s.get("days", days),
            "date": s.get("date"),
            "period": s,
            "overall": {
                "assistant_messages": assistant,
                "all_time_cost": round(total_cost, 4),
                "avg_per_reply": round(total_cost / assistant, 4) if assistant else 0,
            },
        }

    @filter.command("用量", alias={"花费", "费用", "查费用", "查用量"})
    async def cost_cmd(self, event: AstrMessageEvent, days: int = 1):
        """查看 API 用量与费用。用法：/用量 [天数]，默认 1 天。"""
        try:
            days = max(1, min(int(days or 1), 90))
        except Exception:
            days = 1
        yield event.plain_result(summarize(days))

    @filter.llm_tool()
    async def get_api_cost(self, event: AstrMessageEvent, days: int = 1):
        """查询机器人的 API token 用量和大概花费。
        Args:
            days(number): 查询最近多少天，默认 1
        """
        try:
            days = max(1, min(int(days or 1), 90))
        except Exception:
            days = 1
        try:
            return summarize(days)
        except Exception as exc:
            logger.error(f"[cost] 统计失败: {exc}")
            return f"统计失败：{exc}"
