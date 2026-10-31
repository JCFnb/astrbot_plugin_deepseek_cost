import json
import os
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
        # 插件位于 <AstrBot 数据目录>/plugins/<插件名>/main.py
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


def _is_peak(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    h = dt.hour
    return (9 <= h < 12) or (14 <= h < 18)


def _price_for(model: str, dt: datetime) -> dict:
    table = PRICES.get(model or "", DEFAULT_PRICE)
    return table["peak"] if _is_peak(dt) else table["idle"]


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def _iter_rows():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    rows = cur.execute(
        "select provider_model, token_input_other, token_input_cached, token_output, start_time, created_at "
        "from provider_stats where status='completed'"
    ).fetchall()
    con.close()
    for model, tin, tcached, tout, start_time, created_at in rows:
        try:
            if start_time:
                dt = datetime.fromtimestamp(float(start_time), BJ)
            else:
                dt = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ)
        except Exception:
            continue
        yield model or "unknown", int(tin or 0), int(tcached or 0), int(tout or 0), dt


def _collect(days: int) -> dict:
    since = datetime.now(BJ) - timedelta(days=days)
    requests = 0
    total_in = total_cache = total_out = 0
    cost = 0.0
    peak_cost = idle_cost = 0.0
    peak_tokens = idle_tokens = 0
    by_model: dict[str, dict] = {}
    by_day: dict[str, float] = {}
    recent: list[dict] = []

    for model, tin, tcached, tout, dt in _iter_rows():
        if dt < since:
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
        day = dt.strftime("%m-%d")
        by_day[day] = by_day.get(day, 0.0) + c
        recent.append({
            "time": dt.strftime("%m-%d %H:%M"),
            "model": model,
            "input": tin,
            "cache": tcached,
            "output": tout,
            "cost": round(c, 4),
        })

    series = []
    for i in range(days):
        d = (datetime.now(BJ) - timedelta(days=days - 1 - i)).strftime("%m-%d")
        series.append({"day": d, "cost": round(by_day.get(d, 0.0), 4)})

    recent.sort(key=lambda x: x["time"], reverse=True)
    total_tokens = total_in + total_cache + total_out
    return {
        "days": days,
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
    }


def _assistant_count() -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    n = 0
    for (content,) in cur.execute("select content from conversations"):
        try:
            data = json.loads(content or "[]")
        except Exception:
            continue
        n += sum(1 for m in data if isinstance(m, dict) and m.get("role") == "assistant")
    con.close()
    return n


def _all_time_cost() -> float:
    cost = 0.0
    for model, tin, tcached, tout, dt in _iter_rows():
        p = _price_for(model, dt)
        cost += (tin / 1_000_000) * p["miss"] + (tcached / 1_000_000) * p["cache"] + (tout / 1_000_000) * p["out"]
    return cost


def summarize(days: int = 1) -> str:
    s = _collect(days)
    if not s["models"]:
        return f"最近 {days} 天没有 API 调用记录。"
    lines = [f"最近 {days} 天 API 用量："]
    for model, m in sorted(s["models"].items(), key=lambda x: -x[1]["cost"]):
        lines.append(
            f"· {model}：输入 {_fmt_tokens(m['input'])}（缓存命中 {_fmt_tokens(m['cache'])}），"
            f"输出 {_fmt_tokens(m['output'])}，约 ¥{m['cost']:.4f}"
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
        try:
            from quart import request as qreq

            days = int(qreq.args.get("days", 7))
        except Exception:
            days = 7
        days = max(1, min(days, 90))
        s = _collect(days)
        assistant = _assistant_count()
        total_cost = _all_time_cost()
        return {
            "ok": True,
            "days": days,
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
