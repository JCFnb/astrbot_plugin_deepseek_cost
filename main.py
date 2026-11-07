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
}
DEFAULT_PRICE = PRICES["deepseek-flash"]
PRICE_OVERRIDE_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugin_data"
    / "astrbot_plugin_cost"
    / "prices_override.json"
)


def _load_price_overrides() -> dict:
    try:
        if PRICE_OVERRIDE_PATH.is_file():
            data = json.loads(PRICE_OVERRIDE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.warning(f"[cost] 读取自定义价格失败: {exc}")
    return {}


def _save_price_overrides(data: dict) -> None:
    PRICE_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRICE_OVERRIDE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


PRICE_OVERRIDES = _load_price_overrides()


def _is_peak(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    h = dt.hour
    return (9 <= h < 12) or (14 <= h < 18)


def _price_for(model: str, dt: datetime) -> tuple[dict | None, bool]:
    raw = str(model or "").strip().lower()
    key = raw.split("/")[-1]
    table = PRICE_OVERRIDES.get(raw) or PRICE_OVERRIDES.get(key)
    if table is None and (raw.startswith("deepseek") or key.startswith("deepseek")):
        table = PRICES.get(key)
        if table is None:
            for name in sorted(PRICES, key=len, reverse=True):
                if name.startswith("deepseek") and name in key:
                    table = PRICES[name]
                    break
    if table is None:
        return None, False
    rate = table.get("peak") if _is_peak(dt) else table.get("idle")
    if not isinstance(rate, dict):
        rate = table.get("peak") or table.get("idle") or {}
    return rate, True


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


def _reply_count_between(start: datetime, end: datetime | None = None, platform_filter: str = "") -> int:
    """统计指定时间范围内的机器人回复数量。"""
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        end_s = (end or datetime.now(BJ)).strftime("%Y-%m-%d %H:%M:%S")
        sql = "select count(*) from platform_message_history where sender_id='bot' and created_at >= ? and created_at < ?"
        params = [start.strftime("%Y-%m-%d %H:%M:%S"), end_s]
        if platform_filter:
            sql += " and platform_id=?"
            params.append(platform_filter)
        count = cur.execute(sql, params).fetchone()[0]
        con.close()
        return int(count or 0)
    except Exception:
        return 0


def _estimate_replies(start: datetime, end: datetime | None, platform_filter: str = "") -> int:
    """消息历史缺失时，按会话调用占比估算回复数。"""
    all_calls: dict[str, int] = {}
    period_calls: dict[str, int] = {}
    end_dt = end or datetime.now(BJ)
    for _model, _tin, _tcached, _tout, dt, umo in _iter_rows():
        if platform_filter and not str(umo).startswith(platform_filter + ":"):
            continue
        all_calls[umo] = all_calls.get(umo, 0) + 1
        if start <= dt < end_dt:
            period_calls[umo] = period_calls.get(umo, 0) + 1
    total = 0.0
    for umo, count in _reply_counts().items():
        if not all_calls.get(umo):
            continue
        total += count * period_calls.get(umo, 0) / all_calls[umo]
    return int(round(total))


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

    hourly = bool(selected_date) or days == 1
    requests = 0
    total_in = total_cache = total_out = 0
    cost = 0.0
    peak_cost = idle_cost = standard_cost = 0.0
    peak_tokens = idle_tokens = standard_tokens = 0
    by_model: dict[str, dict] = {}
    by_day: dict[str, float] = {}
    by_day_requests: dict[str, int] = {}
    by_day_tokens: dict[str, int] = {}
    bucket_map: dict[str, dict] = {}
    hourly_map: dict[str, dict] = {}
    recent: list[dict] = []
    replies_map = _reply_counts()
    period_replies_history = _reply_count_between(start, end, platform_filter)
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
        price, priced = _price_for(model, dt)
        if price is None:
            c = 0.0
        else:
            c = (tin / 1_000_000) * price["miss"] + (tcached / 1_000_000) * price["cache"] + (tout / 1_000_000) * price["out"]
        cost += c
        requests += 1
        total_in += tin
        total_cache += tcached
        total_out += tout
        tokens = tin + tcached + tout
        bucket_dt = dt.replace(minute=0, second=0, microsecond=0) if hourly else dt.replace(hour=0, minute=0, second=0, microsecond=0)
        bucket_key = bucket_dt.strftime("%Y-%m-%d %H:%M") if hourly else bucket_dt.strftime("%Y-%m-%d")
        bucket = bucket_map.setdefault(bucket_key, {"key": bucket_key, "dt": bucket_dt, "cost": 0.0, "requests": 0, "tokens": 0, "models": {}})
        bucket["cost"] += c
        bucket["requests"] += 1
        bucket["tokens"] += tokens
        bm = bucket["models"].setdefault(model, {"requests": 0, "tokens": 0, "cost": 0.0})
        bm["requests"] += 1
        bm["tokens"] += tokens
        bm["cost"] += c
        hkey = dt.strftime("%Y-%m-%d %H")
        hb = hourly_map.setdefault(hkey, {"dt": dt.replace(minute=0, second=0, microsecond=0), "cost": 0.0, "requests": 0, "tokens": 0})
        hb["cost"] += c
        hb["requests"] += 1
        hb["tokens"] += tokens
        if str(model or "").lower().startswith("deepseek"):
            if _is_peak(dt):
                peak_cost += c
                peak_tokens += tokens
            else:
                idle_cost += c
                idle_tokens += tokens
        else:
            standard_cost += c
            standard_tokens += tokens
        m = by_model.setdefault(model, {"requests": 0, "input": 0, "cache": 0, "output": 0, "cost": 0.0, "priced": priced})
        m["requests"] += 1
        m["input"] += tin
        m["cache"] += tcached
        m["output"] += tout
        m["cost"] += c
        m["priced"] = bool(m.get("priced", True) and priced)
        day = dt.strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0.0) + c
        by_day_requests[day] = by_day_requests.get(day, 0) + 1
        by_day_tokens[day] = by_day_tokens.get(day, 0) + tokens
        recent.append({
            "time": dt.strftime("%m-%d %H:%M"),
            "model": model,
            "input": tin,
            "cache": tcached,
            "output": tout,
            "cost": round(c, 4),
            "priced": priced,
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
    if hourly:
        for i in range(24):
            hdt = start + timedelta(hours=i)
            key = hdt.strftime("%Y-%m-%d %H:%M")
            b = bucket_map.get(key, {"cost": 0.0, "requests": 0, "tokens": 0, "models": {}})
            series.append({
                "key": key,
                "day": hdt.strftime("%H:00"),
                "date": hdt.strftime("%Y-%m-%d"),
                "hour": hdt.hour,
                "label": hdt.strftime("%H:00"),
                "cost": round(b["cost"], 4),
                "requests": b["requests"],
                "tokens": b["tokens"],
                "models": b["models"],
            })
    else:
        for i in range(days):
            ddt = start + timedelta(days=i)
            key = ddt.strftime("%Y-%m-%d")
            b = bucket_map.get(key, {"cost": 0.0, "requests": 0, "tokens": 0, "models": {}})
            hours = []
            for h in range(24):
                hkey = (ddt + timedelta(hours=h)).strftime("%Y-%m-%d %H")
                hb = hourly_map.get(hkey, {"cost": 0.0, "requests": 0, "tokens": 0})
                hours.append({"hour": h, "label": f"{h:02d}:00", "cost": round(hb["cost"], 4), "requests": hb["requests"], "tokens": hb["tokens"]})
            series.append({
                "key": key,
                "day": ddt.strftime("%m-%d"),
                "date": key,
                "hour": None,
                "label": ddt.strftime("%m-%d"),
                "cost": round(b["cost"], 4),
                "requests": b["requests"],
                "tokens": b["tokens"],
                "models": b["models"],
                "hours": hours,
            })

    # 部分平台不写入 platform_message_history，历史不足时按会话调用占比估算。
    replies_estimated = period_replies_history < requests * 0.5
    period_replies = period_replies_history if not replies_estimated else _estimate_replies(start, end, platform_filter)
    if not period_replies:
        period_replies = requests if requests else 0
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
        "assistant_messages": period_replies,
        "replies_estimated": replies_estimated,
        "avg_request": round(cost / requests, 4) if requests else 0,
        "avg_per_reply": round(cost / period_replies, 4) if period_replies else 0,
        "cache_rate": round(total_cache / total_tokens * 100, 1) if total_tokens else 0,
        "peak": {"cost": round(peak_cost, 4), "tokens": peak_tokens},
        "idle": {"cost": round(idle_cost, 4), "tokens": idle_tokens},
        "standard": {"cost": round(standard_cost, 4), "tokens": standard_tokens},
        "models": by_model,
        "series": series,
        "buckets": series,
        "hourly": hourly,
        "recent": recent,
        "recent_total": len(recent),
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
        price, priced = _price_for(model, dt)
        if price is None:
            continue
        cost += (tin / 1_000_000) * price["miss"] + (tcached / 1_000_000) * price["cache"] + (tout / 1_000_000) * price["out"]
    return cost


def summarize(days: int = 1, date_str: str | None = None) -> str:
    s = _collect(days, date_str)
    label = s.get("label") or f"最近 {days} 天"
    if not s["models"]:
        return f"{label}没有 API 调用记录。"
    lines = [f"{label} API 用量（{s.get('start', '')} 至 {s.get('end', '')}）："]
    for model, m in sorted(s["models"].items(), key=lambda x: -x[1]["cost"]):
        cost_text = f"约 ¥{m['cost']:.4f}" if m.get("priced", True) else "价格未配置"
        lines.append(
            f"· {model}：输入 {_fmt_tokens(m['input'])}（缓存命中 {_fmt_tokens(m['cache'])}），"
            f"输出 {_fmt_tokens(m['output'])}，{cost_text}"
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
            self.context.register_web_api(
                "/astrbot_plugin_cost/prices",
                self._prices_api,
                ["GET", "POST"],
                "模型参考价格",
            )
        except Exception as exc:
            logger.warning(f"[cost] 注册 Web API 失败: {exc}")

    async def _prices_api(self):
        from quart import request as qreq

        if qreq.method == "POST":
            try:
                body = await qreq.get_json(silent=True) or {}
            except Exception:
                body = {}
            if body.get("reset"):
                PRICE_OVERRIDES.clear()
                _save_price_overrides(PRICE_OVERRIDES)
            else:
                raw_models = body.get("models")
                if not isinstance(raw_models, dict):
                    return {"ok": False, "error": "models 必须是对象"}
                cleaned = {}
                for name, table in raw_models.items():
                    model_name = str(name or "").strip()
                    if not model_name or not isinstance(table, dict):
                        continue
                    peak = table.get("peak") if isinstance(table.get("peak"), dict) else table
                    idle = table.get("idle") if isinstance(table.get("idle"), dict) else table
                    def rates(src):
                        out = {}
                        for key in ("cache", "miss", "out"):
                            try:
                                value = float(src.get(key, 0) or 0)
                            except (TypeError, ValueError):
                                value = 0
                            out[key] = max(0.0, round(value, 6))
                        return out
                    cleaned[model_name.lower()] = {
                        "peak": rates(peak),
                        "idle": rates(idle),
                    }
                PRICE_OVERRIDES.clear()
                PRICE_OVERRIDES.update(cleaned)
                _save_price_overrides(PRICE_OVERRIDES)

        models = []
        names = [name for name in PRICES if name.lower().startswith("deepseek")]
        for name in PRICE_OVERRIDES:
            if name not in names:
                names.append(name)
        for name in names:
            custom = name.lower() in PRICE_OVERRIDES
            table = PRICE_OVERRIDES.get(name.lower()) or PRICES.get(name) or DEFAULT_PRICE
            models.append({
                "model": name,
                "unit": "CNY / 1M tokens",
                "peak": table.get("peak", {}),
                "idle": table.get("idle", {}),
                "source": "自定义参考价" if custom else "内置参考价（估算）",
                "custom": custom,
            })
        return {
            "ok": True,
            "updated": "2026-11-08",
            "unit": "CNY / 1M tokens",
            "note": "参考价仅用于本地估算，实际扣费以各模型服务商账单为准。",
            "models": models,
        }


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
