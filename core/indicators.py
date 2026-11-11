# -*- coding: utf-8 -*-
"""技术指标与形态判定：布林线 + 「连跌破下轨后阳线收复」。"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

import config


def bollinger(
    close: pd.Series | np.ndarray,
    window: int | None = None,
    mult: float | None = None,
) -> pd.DataFrame:
    """
    布林带 BOLL：中轨 = MA(N)，上/下轨 = 中轨 ± K * 总体标准差。
    rolling 窗口只使用当前及之前的收盘价，不含未来数据。
    """
    window = window or config.BOLL_WINDOW
    mult = config.BOLL_STD_MULT if mult is None else mult
    s = pd.Series(np.asarray(close, dtype=float), dtype=float)
    ma = s.rolling(window, min_periods=window).mean()
    std = s.rolling(window, min_periods=window).std(ddof=0)
    return pd.DataFrame(
        {"mid": ma, "std": std, "upper": ma + mult * std, "lower": ma - mult * std}
    )


@dataclass
class PatternParams:
    """形态参数快照，回测与实盘共用同一套，避免口径不一致。"""

    min_break_days: int = config.MIN_BREAK_DAYS
    max_cont_lookback: int = config.MAX_CONT_LOOKBACK
    strict_decline: bool = config.REQUIRE_STRICT_DECLINE
    min_decline_pct: float = config.MIN_DECLINE_PCT
    min_yang_pct: float = config.MIN_YANG_PCT
    max_yang_pct: float = config.MAX_YANG_PCT
    require_close_above_lower: bool = config.REQUIRE_CLOSE_ABOVE_LOWER
    require_low_touch_lower: bool = config.REQUIRE_LOW_TOUCH_LOWER
    require_volume_up: bool = config.REQUIRE_VOLUME_UP

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class KLineView:
    """预转换成 numpy 的 K 线视图，供历史回测逐日高速判定。"""

    n: int
    close: np.ndarray
    open: np.ndarray
    low: np.ndarray
    vol: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    mid: np.ndarray


def make_view(df: pd.DataFrame, bands: pd.DataFrame) -> KLineView:
    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    return KLineView(
        n=n,
        close=close,
        open=df["open"].to_numpy(dtype=float) if "open" in df else close,
        low=df["low"].to_numpy(dtype=float) if "low" in df else close,
        vol=df["volume"].to_numpy(dtype=float) if "volume" in df else np.zeros(n),
        lower=bands["lower"].to_numpy(dtype=float),
        upper=bands["upper"].to_numpy(dtype=float),
        mid=bands["mid"].to_numpy(dtype=float),
    )


def _empty_detail() -> dict:
    return {
        "break_days": 0, "decline_pct": 0.0, "yang_pct": 0.0,
        "lower": None, "upper": None, "mid": None, "boll_pos": None, "volume_ratio": None,
    }


def check_reversal_fast(
    v: KLineView, i: int, params: PatternParams | None = None
) -> tuple[bool, dict]:
    """
    判断第 i 根 K 线是否为「连续下跌跌破布林下轨后，一根阳线收复」信号。

    条件（config.py 可调）：
      1. 第 i 根为阳线 close > open，且相对昨收涨幅 ∈ [min_yang_pct, max_yang_pct]
      2. 收盘重新站上布林下轨（默认要求）；可选要求盘中最低触及下轨
      3. 从 i-1 往前连续 >= min_break_days 根收盘 <= 当期下轨，且逐日走低
      4. 破轨区间累计跌幅 >= min_decline_pct
      5. 可选放量确认
    """
    p = params or PatternParams()
    empty = _empty_detail()
    if i < config.BOLL_WINDOW + p.min_break_days + 1 or i >= v.n:
        return False, empty
    close, open_, low, vol, lower, upper = (
        v.close, v.open, v.low, v.vol, v.lower, v.upper
    )
    if not np.isfinite(lower[i]) or not np.isfinite(close[i - 1]) or close[i - 1] <= 0:
        return False, empty

    yang_pct = (close[i] / close[i - 1] - 1.0) * 100.0
    if close[i] <= open_[i]:
        return False, empty
    if yang_pct < p.min_yang_pct or yang_pct > p.max_yang_pct:
        return False, empty
    if p.require_close_above_lower and close[i] <= lower[i]:
        return False, empty
    if p.require_low_touch_lower and low[i] > lower[i]:
        return False, empty
    if p.require_volume_up and (vol[i] <= vol[i - 1] * 1.1 or vol[i - 1] <= 0):
        return False, empty

    cnt, j = 0, i - 1
    while j >= 1 and cnt < p.max_cont_lookback:
        if not np.isfinite(lower[j]):
            break
        below = close[j] <= lower[j]
        falling = (not p.strict_decline) or close[j] < close[j - 1]
        if below and falling:
            cnt += 1
            j -= 1
        else:
            break
    if cnt < p.min_break_days:
        return False, empty

    seg_start = j + 1
    ref = close[seg_start - 1] if seg_start >= 1 else close[seg_start]
    decline_pct = (close[i - 1] / ref - 1.0) * 100.0 if ref > 0 else 0.0
    if p.strict_decline and decline_pct > -abs(p.min_decline_pct):
        return False, empty

    span = upper[i] - lower[i]
    boll_pos = (close[i] - lower[i]) / span if span and np.isfinite(span) and span > 0 else None
    detail = {
        "break_days": int(cnt),
        "decline_pct": round(float(decline_pct), 2),
        "yang_pct": round(float(yang_pct), 2),
        "lower": round(float(lower[i]), 3),
        "upper": round(float(upper[i]), 3),
        "mid": round(float(v.mid[i]), 3),
        "boll_pos": round(float(boll_pos), 3) if boll_pos is not None else None,
        "volume_ratio": round(float(vol[i] / vol[i - 1]), 2) if vol[i - 1] > 0 else None,
    }
    return True, detail


def check_reversal(
    df: pd.DataFrame,
    bands: pd.DataFrame,
    i: int,
    params: PatternParams | None = None,
) -> tuple[bool, dict]:
    """便捷封装：直接传 DataFrame。逐日回测请改用 make_view + check_reversal_fast。"""
    return check_reversal_fast(make_view(df, bands), i, params)


def make_today_bar(row: dict) -> dict:
    """把 14:30 的实时快照转换成一根「当日临时 K 线」。"""
    return {
        "date": str(row.get("date", "today")),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["price"]),
        "volume": float(row.get("volume", 0) or 0),
        "amount": float(row.get("amount", 0) or 0),
    }
