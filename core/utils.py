# -*- coding: utf-8 -*-
"""通用工具：日志、交易日判断、JSON 原子读写。"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import config

CN_TZ = ZoneInfo(config.TIMEZONE)
_LOGGER_READY = False


def setup_logging(level=logging.INFO) -> logging.Logger:
    """配置全局日志（同时输出到控制台和 logs/app.log）。"""
    global _LOGGER_READY
    root = logging.getLogger()
    if _LOGGER_READY:
        return root
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = RotatingFileHandler(
        config.LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    _LOGGER_READY = True
    return root


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def today_cn() -> date:
    return now_cn().date()


def _load_local_holidays() -> set[str]:
    """读取用户自定义休市日文件 data/holidays.txt（每行一个 YYYY-MM-DD）。"""
    p = config.DATA_DIR / "holidays.txt"
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def load_trade_calendar(force_refresh: bool = False) -> set[str] | None:
    """
    获取 A 股交易日历（YYYY-MM-DD 字符串集合）。
    优先使用 AkShare 在线日历并落盘缓存；失败返回 None，由调用方降级为工作日判断。
    """
    cache_file = config.DATA_DIR / "trade_calendar.json"
    if cache_file.exists() and not force_refresh:
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("year") == date.today().year and data.get("days"):
                return set(data["days"])
        except Exception:
            pass
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        days = {str(x) for x in df["trade_date"].astype(str).tolist()}
        cache_file.write_text(
            json.dumps({"year": date.today().year, "days": sorted(days)}, ensure_ascii=False),
            encoding="utf-8",
        )
        return days
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("在线交易日历获取失败，改用工作日判断: %s", exc)
        return None


def is_trading_day(day: date | None = None, calendar: set[str] | None = None) -> bool:
    """是否 A 股交易日。calendar 为 None 时用工作日 + 自定义休市日近似。"""
    day = day or today_cn()
    ds = day.isoformat()
    if calendar is None:
        if day.weekday() >= 5:
            return False
        return ds not in _load_local_holidays()
    return ds in calendar


def safe_float(v, default=0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
            return default
        return f
    except (TypeError, ValueError):
        return default


def atomic_write_json(path: Path, obj) -> None:
    """原子写 JSON，避免调度线程写一半被网页读到。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_json(path: Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default
