# -*- coding: utf-8 -*-
"""股票池过滤 + 全市场形态扫描。"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date

import numpy as np
import pandas as pd

import config
from core.indicators import PatternParams, bollinger, check_reversal, make_today_bar
from core.utils import safe_float

log = logging.getLogger(__name__)


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


def scan(
    snap: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
    meta: dict,
    params: PatternParams | None = None,
) -> tuple[list[dict], dict]:
    """
    扫描全市场，返回命中「连跌破布林下轨 → 阳线收复」的候选列表。
    当日 K 线由 14:30 实时快照构造，历史日线取到「昨天」。
    """
    p = params or PatternParams()
    filtered, filter_stats = apply_filters(snap, histories, meta.get("market_date", ""))
    candidates: list[dict] = []
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
        bands = bollinger(full["close"])
        ok, detail = check_reversal(full, bands, len(full) - 1, p)
        if not ok:
            continue

        n = len(full)
        s = max(0, n - 45)
        spark = {
            "dates": [str(x)[:10] for x in full["date"].iloc[s:].tolist()],
            "close": [round(float(x), 2) for x in full["close"].iloc[s:].tolist()],
            "lower": [round(float(x), 2) if np.isfinite(x) else None for x in bands["lower"].iloc[s:].tolist()],
            "upper": [round(float(x), 2) if np.isfinite(x) else None for x in bands["upper"].iloc[s:].tolist()],
            "mid": [round(float(x), 2) if np.isfinite(x) else None for x in bands["mid"].iloc[s:].tolist()],
        }
        candidates.append(
            {
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
                "spark": spark,
                **detail,
            }
        )

    stats = {
        "universe_total": int(len(snap)),
        "after_filter": int(len(filtered)),
        "scanned": scanned,
        "signals": len(candidates),
        "filter_detail": filter_stats,
        "params": p.as_dict(),
        "boll": {"window": config.BOLL_WINDOW, "mult": config.BOLL_STD_MULT},
    }
    log.info("扫描完成: 全市场 %d → 过滤后 %d → 命中 %d", len(snap), len(filtered), len(candidates))
    return candidates, stats
