# -*- coding: utf-8 -*-
"""股票池过滤 + 全市场形态扫描 + 超跌观察榜。"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date

import numpy as np
import pandas as pd

import config
from core.indicators import (
    PatternParams, bollinger, build_kline_series, check_reversal, make_today_bar,
)
from core.utils import safe_float

log = logging.getLogger(__name__)

# 超跌观察榜：给手动筛选用的「接近下轨」候选，不影响严格命中结果
WATCH_TOP_N = 20            # 最多列出多少只
WATCH_BOLL_MAX = 0.35       # 收盘布林位置 <= 该值才算「贴近下轨区」
WATCH_BOLL_FALLBACK = 0.55  # 不足 WATCH_TOP_N 时放宽到的阈值
WATCH_KLINE_DAYS = 60       # 观察榜 K 线根数（控制 data.js 体积）


def apply_filters(
    snap: pd.DataFrame, histories: dict[str, pd.DataFrame], market_date: str
) -> tuple[pd.DataFrame, dict]:
    """剔除科创板/北交所/ST/停牌/低流动性/上市过短的股票。"""
    stats: Counter = Counter()
    keep_codes: list[str] = []
    try:
        mdate = date.fromisoformat(market_date[:10])
    except Exception:
        mdate = None
    kw = [k.upper() for k in config.EXCLUDE_NAME_KEYWORDS]

    for row in snap.to_dict("records"):
        code = str(row.get("code", "")).zfill(6)
        name = str(row.get("name", "") or "")
        if code.startswith(tuple(config.EXCLUDE_PREFIXES)):
            stats["科创板/北交所"] += 1
            continue
        if any(k in name.upper() for k in kw):
            stats["ST/退市"] += 1
            continue
        price = safe_float(row.get("price"), 0)
        pre_close = safe_float(row.get("pre_close"), 0)
        amount = safe_float(row.get("amount"), 0)
        volume = safe_float(row.get("volume"), 0)
        if price < config.MIN_PRICE or price > config.MAX_PRICE or pre_close <= 0:
            stats["价格异常"] += 1
            continue
        if amount < config.MIN_AMOUNT or volume <= 0:
            stats["成交额不足/停牌"] += 1
            continue
        hist = histories.get(code)
        if hist is None or len(hist) < config.MIN_LIST_DAYS:
            stats["历史数据不足"] += 1
            continue
        if mdate is not None:
            try:
                last = date.fromisoformat(str(hist["date"].iloc[-1])[:10])
                if (mdate - last).days > 12:   # 长期停牌
                    stats["长期停牌"] += 1
                    continue
            except Exception:
                pass
        keep_codes.append(code)

    filtered = snap[snap["code"].astype(str).str.zfill(6).isin(set(keep_codes))].copy()
    return filtered, dict(stats)


def _observe(full: pd.DataFrame, bands: pd.DataFrame, i: int, p: PatternParams) -> dict:
    """计算单只股票的「超跌」形态指标，不判定是否命中。

    复用与 check_reversal 相同的破轨口径，另加距下轨距离、布林位置、近 5 日跌幅，
    供超跌观察榜排序与人工挑选。全部只读已加载的序列，不产生任何网络请求。
    """
    close = full["close"].to_numpy(dtype=float)
    open_ = full["open"].to_numpy(dtype=float) if "open" in full else close
    lower = bands["lower"].to_numpy(dtype=float)
    upper = bands["upper"].to_numpy(dtype=float)
    if i < config.BOLL_WINDOW + 1 or i >= len(close):
        return {}
    if not np.isfinite(lower[i]) or lower[i] <= 0 or not np.isfinite(close[i - 1]):
        return {}

    yang_pct = (close[i] / close[i - 1] - 1.0) * 100.0 if close[i - 1] > 0 else 0.0
    is_yang = bool(close[i] > open_[i])
    gap_pct = (close[i] / lower[i] - 1.0) * 100.0
    span = upper[i] - lower[i]
    boll_pos = (close[i] - lower[i]) / span if np.isfinite(span) and span > 0 else 0.5

    j5 = max(0, i - 5)
    ret5 = (close[i] / close[j5] - 1.0) * 100.0 if close[j5] > 0 else 0.0

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
    seg_start = j + 1
    ref = close[seg_start - 1] if seg_start >= 1 else close[seg_start]
    decline_pct = (close[i - 1] / ref - 1.0) * 100.0 if ref > 0 else 0.0

    return {
        "break_days": int(cnt),
        "decline_pct": round(float(decline_pct), 2),
        "yang_pct": round(float(yang_pct), 2),
        "gap_pct": round(float(gap_pct), 2),
        "boll_pos": round(float(boll_pos), 3),
        "ret5": round(float(ret5), 2),
        "is_yang": is_yang,
        "lower": round(float(lower[i]), 3),
        "upper": round(float(upper[i]), 3),
        "mid": round(float(bands["mid"].iloc[i]), 3),
    }


def _watch_note(o: dict, p: PatternParams) -> str:
    """用一句话说明这只票「离确认信号还差在哪」，供人工判断。"""
    gap = o["gap_pct"]
    bits = []
    if gap < 0:
        bits.append(f"仍在线下 {gap:.2f}%（未站回下轨）")
    else:
        bits.append(f"贴近下轨 +{gap:.2f}%")
    if o["break_days"] >= p.min_break_days and o["decline_pct"] <= -abs(p.min_decline_pct):
        bits.append(f"前期连破 {o['break_days']} 天 · 跌 {o['decline_pct']:.2f}%")
    elif o["break_days"] > 0:
        bits.append(f"仅破轨 {o['break_days']} 天")
    else:
        bits.append("前期未连续破轨")
    if not o["is_yang"]:
        bits.append("当日收阴")
    elif not (p.min_yang_pct <= o["yang_pct"] <= p.max_yang_pct):
        bits.append(f"涨幅 {o['yang_pct']:.2f}% 越界")
    return " · ".join(bits)


def scan(
    snap: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    meta: dict,
    params: PatternParams | None = None,
) -> tuple[list[dict], dict, list[dict]]:
    """扫描全市场，返回 (严格命中候选, 统计, 超跌观察榜)。

    - 严格命中：连跌破布林下轨 → 阳线收复，用于 Top10 与历史胜率排序。
    - 超跌观察榜：贴近/跌破下轨但尚未确认反转的股票，按超跌程度排序，
      供手动模式下人工挑选。两者互不干扰，观察榜不改变任何命中结果。
    """
    p = params or PatternParams()
    filtered, filter_stats = apply_filters(snap, histories, meta.get("market_date", ""))
    candidates: list[dict] = []
    observed: list[dict] = []
    scanned = 0
    market_date = meta.get("market_date", "")

    for row in filtered.to_dict("records"):
        code = str(row["code"]).zfill(6)
        hist = histories.get(code)
        if hist is None:
            continue
        scanned += 1
        tail = hist.tail(160).reset_index(drop=True)
        bar = make_today_bar(row)
        bar["date"] = market_date
        try:
            full = pd.concat([tail, pd.DataFrame([bar])], ignore_index=True)
        except Exception:
            continue
        i = len(full) - 1
        bands = bollinger(full["close"])
        m = _observe(full, bands, i, p)
        if not m:
            continue
        ok, detail = check_reversal(full, bands, i, p)

        base = {
            "code": code,
            "name": str(row.get("name", "")),
            "price": round(safe_float(row.get("price")), 2),
            "pct_chg": round(safe_float(row.get("pct_chg")), 2),
            "pre_close": round(safe_float(row.get("pre_close")), 2),
            "open": round(safe_float(row.get("open")), 2),
            "high": round(safe_float(row.get("high")), 2),
            "low": round(safe_float(row.get("low")), 2),
            "amount": round(safe_float(row.get("amount")), 0),
            "turnover": round(safe_float(row.get("turnover")), 2),
            "market_cap": round(safe_float(row.get("market_cap")), 0),
            "hist_len": int(len(hist)),
            "last_hist_date": str(hist["date"].iloc[-1])[:10],
        }

        if ok:
            # 完整日 K 序列（OHLCV + BOLL + MA + MACD），供网页画同花顺式 K 线
            spark = build_kline_series(full, bands, n_days=90)
            candidates.append({**base, "spark": spark, **detail})

        # 观察榜：含 K 线，点击可看同花顺式日 K
        observed.append({
            **base, **m,
            "hit": bool(ok),
            "spark": build_kline_series(full, bands, n_days=WATCH_KLINE_DAYS),
        })

    # 超跌观察榜：排除已严格命中的，只留贴近下轨区，按距下轨 / 近 5 日跌幅排序
    pool = [o for o in observed if not o["hit"] and o["boll_pos"] <= WATCH_BOLL_MAX]
    if len(pool) < WATCH_TOP_N:
        pool = [o for o in observed if not o["hit"] and o["boll_pos"] <= WATCH_BOLL_FALLBACK]
    pool.sort(key=lambda o: (o["gap_pct"], o["ret5"], -o["break_days"]))
    watchlist = []
    for o in pool[:WATCH_TOP_N]:
        x = {k: v for k, v in o.items() if k != "hit"}
        x["note"] = _watch_note(o, p)
        watchlist.append(x)

    stats = {
        "universe_total": int(len(snap)),
        "after_filter": int(len(filtered)),
        "scanned": scanned,
        "signals": len(candidates),
        "watchlist": len(watchlist),
        "watch_pool": len(pool),
        "filter_detail": filter_stats,
        "params": p.as_dict(),
        "boll": {"window": config.BOLL_WINDOW, "mult": config.BOLL_STD_MULT},
    }
    log.info(
        "扫描完成: 全市场 %d → 过滤后 %d → 命中 %d → 超跌观察 %d（候选池 %d）",
        len(snap), len(filtered), len(candidates), len(watchlist), len(pool),
    )
    return candidates, stats, watchlist
