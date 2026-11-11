# -*- coding: utf-8 -*-
"""
确定性演示数据源。

生成带真实感的 A 股日线（几何布朗运动 + 牛熊分段 + 波动率聚集），
并在历史中注入若干次「连续跌破布林下轨 → 阳线收复」形态；
形态之后的走势按该股预设胜率生成，因此历史胜率回测有真实的区分度。

固定随机种子 => 每次运行结果一致，便于验证策略与页面。
仅用于开发/演示，实盘请设置 STOCK_DATA_SOURCE=akshare。
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

import config
from core.indicators import bollinger, check_reversal

_RNG_SEED = 20261111

_PREFIX = ["华", "东", "中", "南", "海", "恒", "瑞", "嘉", "金", "远", "新", "同", "力", "天", "长", "兴"]
_MIDDLE = ["远", "方", "盛", "信", "通", "达", "源", "泰", "科", "创", "安", "联", "生", "辰", "茂", "博"]
_SUFFIX = ["科技", "股份", "智能", "精工", "电子", "材料", "医药", "能源", "重工", "实业", "信息", "装备"]


def _trade_days(end: date, n: int) -> list[date]:
    """倒推 n 个工作日作为交易日（演示用，忽略法定节假日）。"""
    days: list[date] = []
    cur = end
    while len(days) < n:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= timedelta(days=1)
    return list(reversed(days))


def _make_codes(rng: np.random.Generator, n: int) -> list[tuple[str, str]]:
    pools = ["600", "601", "603", "605", "000", "001", "002", "300", "301"]
    out, used = [], set()
    # 故意混入科创板 / 北交所代码，用于验证过滤规则确实生效
    for c in ["688001", "688126", "688399", "688561", "830001", "430047", "920157", "920045"]:
        out.append((c, "测试" + c[-3:]))
        used.add(c)
    while len(out) < n:
        p = pools[int(rng.integers(0, len(pools)))]
        c = p + f"{int(rng.integers(0, 999)):03d}"
        if c in used:
            continue
        used.add(c)
        name = (
            _PREFIX[int(rng.integers(0, len(_PREFIX)))]
            + _MIDDLE[int(rng.integers(0, len(_MIDDLE)))]
            + _SUFFIX[int(rng.integers(0, len(_SUFFIX)))]
        )
        if rng.random() < 0.04:
            name = "ST" + name
        out.append((c, name))
    return out


def _inject_patterns(rets: np.ndarray, rng: np.random.Generator, target_wr: float, hold: int) -> list[int]:
    """在收益率序列中注入若干次「连跌破轨 + 阳线收复」形态，返回阳线位置。"""
    n = len(rets)
    positions: list[int] = []
    cursor = 70
    while cursor < n - hold - 20:
        cursor += int(rng.integers(hold + 12, hold + 55))
        if cursor >= n - hold - 20:
            break
        k = int(rng.integers(config.MIN_BREAK_DAYS, config.MIN_BREAK_DAYS + 3))
        for j in range(cursor - k, cursor):
            rets[j] = -float(rng.uniform(0.022, 0.046))
        rets[cursor] = float(rng.uniform(0.035, 0.072))
        win = rng.random() < target_wr
        drift = 0.0075 if win else -0.0065
        for j in range(cursor + 1, min(cursor + 1 + hold, n)):
            rets[j] = float(rng.normal(drift, 0.011))
        positions.append(cursor)
        cursor += hold
    return positions


def _build_ohlc(close: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    n = len(close)
    prev = np.concatenate([[close[0]], close[:-1]])
    pct = close / prev - 1.0
    open_ = prev * (1 + rng.normal(0, 0.004, n))
    open_ = np.where(pct >= 0, np.minimum(open_, close * 0.996), np.maximum(open_, close * 1.004))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol = np.abs(rng.lognormal(15.6, 0.5, n))
    amount = vol * close * (1 + rng.uniform(0.01, 0.05, n))
    return pd.DataFrame(
        {"open": open_, "high": hi, "low": lo, "close": close, "volume": vol, "amount": amount}
    )


def _shape_tail(df: pd.DataFrame, depth: float, rng: np.random.Generator) -> pd.DataFrame:
    """把历史最后若干天改造成「逐日加速下跌、收盘跌破布林下轨」的形态。"""
    trial = df.copy()
    n = len(trial)
    k = config.MIN_BREAK_DAYS + 1
    close = trial["close"].to_numpy(dtype=float).copy()
    open_ = trial["open"].to_numpy(dtype=float).copy()
    high = trial["high"].to_numpy(dtype=float).copy()
    low = trial["low"].to_numpy(dtype=float).copy()
    for j, idx in enumerate(range(n - k, n)):
        prev_c = close[idx - 1]
        noise = float(rng.uniform(0.82, 1.28))
        c = prev_c * (1 - (0.026 + j * 0.006) * depth * noise)
        close[idx] = c
        open_[idx] = prev_c * 1.002
        high[idx] = max(prev_c, c) * 1.004
        low[idx] = c * 0.992
    trial["close"], trial["open"] = close, open_
    trial["high"], trial["low"] = high, low
    return trial


def generate(end_day: date | None = None, n_stocks: int = 180) -> tuple[pd.DataFrame, dict, dict]:
    """
    返回 (全市场快照 DataFrame, {code: 历史日线 DataFrame}, meta)。
    历史日线最后一根是「昨天」，快照代表「今天 14:30」的盘中状态。
    """
    rng = np.random.default_rng(_RNG_SEED)
    end_day = end_day or date.today()
    yesterday = end_day - timedelta(days=1)
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)
    today = yesterday + timedelta(days=1)
    while today.weekday() >= 5:
        today += timedelta(days=1)

    n_hist = max(320, config.BACKTEST_YEARS * 250 + 40)
    days = _trade_days(yesterday, n_hist)
    pairs = _make_codes(rng, n_stocks)
    history: dict[str, pd.DataFrame] = {}
    rows: list[dict] = []
    shaped = 0

    for idx, (code, name) in enumerate(pairs):
        r = np.random.default_rng(_RNG_SEED + idx * 7919)
        target_wr = float(np.clip(r.normal(0.52, 0.19), 0.20, 0.86))
        base_drift = float(r.normal(0.0001, 0.0006))
        vol = float(r.uniform(0.013, 0.027))

        rets = r.normal(base_drift, vol, n_hist)
        seg = 0
        while seg < n_hist:
            seg_len = int(r.integers(25, 90))
            rets[seg: seg + seg_len] += r.normal(0, 0.0022)
            seg += seg_len
        rets *= 1 + 0.35 * np.sin(np.arange(n_hist) / 23.0)
        _inject_patterns(rets, r, target_wr, config.HOLD_DAYS)

        start_price = float(r.uniform(6, 68))
        close = start_price * np.exp(np.cumsum(np.clip(rets, -0.095, 0.095)))
        close = np.clip(close, 2.2, 480.0)
        df = _build_ohlc(close, r)
        df.insert(0, "date", [d.isoformat() for d in days])

        # 约 42% 的股票在末端塑造成「今日 14:30 阳线收复」形态，保证有候选
        force_today = r.random() < 0.45
        shaped_row = None
        if force_today:
            for depth in (1.0, 1.3, 1.6, 1.9, 2.2, 2.6):
                trial = _shape_tail(df, depth, r)
                last = float(trial["close"].iloc[-1])
                pct = float(r.uniform(2.2, 6.8))
                open_t = last * float(r.uniform(0.978, 0.994))
                close_t = last * (1 + pct / 100.0)
                bar = {
                    "date": today.isoformat(),
                    "open": open_t,
                    "high": close_t * 1.004,
                    "low": min(open_t, last * 0.985),
                    "close": close_t,
                    "volume": float(r.uniform(3e6, 3e7)),
                    "amount": float(r.uniform(4e7, 2.4e9)),
                }
                full = pd.concat([trial, pd.DataFrame([bar])], ignore_index=True)
                ok, _ = check_reversal(full, bollinger(full["close"]), len(full) - 1)
                if ok:
                    df = trial
                    shaped_row = (last, pct, open_t, close_t, bar)
                    shaped += 1
                    break
            if shaped_row is None:
                force_today = False

        history[code] = df
        last = float(df["close"].iloc[-1])

        if shaped_row:
            last, pct, open_t, close_t, bar = shaped_row
            high_t, low_t, amount = float(bar["high"]), float(bar["low"]), float(bar["amount"])
            volume = float(bar["volume"])
        else:
            pct = float(np.clip(r.normal(0, 2.6), -9.8, 9.8))
            open_t = last * (1 + float(r.normal(0, 0.006)))
            close_t = last * (1 + pct / 100.0)
            high_t = max(open_t, close_t) * 1.004
            low_t = min(open_t, close_t) * 0.996
            amount = float(r.uniform(4e7, 2.4e9))
            volume = amount / max(close_t, 0.01)

        rows.append(
            {
                "code": code,
                "name": name,
                "price": round(close_t, 2),
                "open": round(open_t, 2),
                "high": round(high_t, 2),
                "low": round(low_t, 2),
                "pre_close": round(last, 2),
                "pct_chg": round(pct, 2),
                "amount": round(amount, 0),
                "volume": round(volume, 0),
                "turnover": round(float(r.uniform(0.5, 12)), 2),
                "market_cap": round(float(r.uniform(3e9, 8e10)), 0),
            }
        )

    snapshot = pd.DataFrame(rows)
    meta = {
        "market_date": today.isoformat(),
        "source": "demo",
        "universe_total": len(snapshot),
        "shaped_today": shaped,
    }
    return snapshot, history, meta
