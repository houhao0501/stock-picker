# -*- coding: utf-8 -*-
"""
历史胜率回测：对每只候选股，找出历史上所有同类信号，
统计信号后持有 HOLD_DAYS 个交易日的收益，得到胜率与平均收益。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np

import config
from core.indicators import PatternParams, bollinger, check_reversal_fast, make_view

log = logging.getLogger(__name__)


def find_signals(hist, params: PatternParams | None = None, hold_days: int | None = None) -> list[dict]:
    """在纯历史日线上找出所有同类信号及其持有期收益。"""
    p = params or PatternParams()
    hold = hold_days or config.HOLD_DAYS
    n = len(hist)
    start = config.BOLL_WINDOW + p.min_break_days + 1
    if n <= start + hold + 2:
        return []
    close = hist["close"].to_numpy(dtype=float)
    dates = hist["date"].astype(str).tolist()
    v = make_view(hist, bollinger(hist["close"]))
    out: list[dict] = []
    last_i = -999
    for i in range(start, n - hold):
        if i - last_i < 5:            # 冷却期，避免同一形态被重复计数
            continue
        ok, _ = check_reversal_fast(v, i, p)
        if not ok:
            continue
        c0 = close[i]
        if c0 <= 0 or not np.isfinite(c0):
            continue
        ret = (close[i + hold] / c0 - 1.0) * 100.0
        out.append(
            {
                "date": dates[i][:10],
                "signal_close": round(float(c0), 2),
                "exit_date": dates[i + hold][:10],
                "return_pct": round(float(ret), 2),
                "win": bool(ret > 0),
            }
        )
        last_i = i
    return out


def evaluate(candidate: dict, hist, params: PatternParams | None = None) -> dict:
    """为单只候选股计算胜率指标（贝叶斯平滑，抑制小样本虚高）。"""
    p = params or PatternParams()
    signals = find_signals(hist, p)
    count = len(signals)
    wins = sum(1 for s in signals if s["win"])
    rets = [s["return_pct"] for s in signals]
    avg = float(np.mean(rets)) if rets else 0.0
    win_rate_raw = wins / count if count else config.PRIOR_WIN_RATE
    # 贝叶斯平滑：(胜次 + 先验权重*先验胜率) / (样本数 + 先验权重)
    win_rate = (wins + config.PRIOR_WEIGHT * config.PRIOR_WIN_RATE) / (count + config.PRIOR_WEIGHT)
    sample_ok = count >= config.MIN_SIGNALS

    score = (
        win_rate * 100.0 * 0.72
        + float(np.clip(avg, -15.0, 25.0)) * 1.1
        + min(count, 12) * 0.5
    )
    if not sample_ok:
        score -= 15.0

    last = signals[-1] if signals else None
    return {
        "win_rate": round(float(win_rate), 4),
        "win_rate_raw": round(float(win_rate_raw), 4),
        "signal_count": count,
        "wins": wins,
        "avg_return": round(avg, 2),
        "best_return": round(max(rets), 2) if rets else 0.0,
        "worst_return": round(min(rets), 2) if rets else 0.0,
        "sample_ok": bool(sample_ok),
        "score": round(float(score), 2),
        "hold_days": config.HOLD_DAYS,
        "last_signal": last,
        "recent_signals": signals[-6:],
    }


def enrich(
    candidates: list[dict],
    histories: dict[str, pd.DataFrame],
    params: PatternParams | None = None,
) -> tuple[list[dict], dict]:
    """给所有候选附加胜率指标并按综合分排序，标记 TopN。"""
    p = params or PatternParams()
    if len(candidates) > config.MAX_CANDIDATES:
        # 候选过多时优先保留形态更极端的（破轨天数多、跌幅大）
        candidates = sorted(
            candidates,
            key=lambda x: (x.get("break_days", 0), -x.get("decline_pct", 0)),
            reverse=True,
        )[: config.MAX_CANDIDATES]
        log.info("候选数超过上限，截取 %d 只进入回测", len(candidates))

    out = []
    for c in candidates:
        hist = histories.get(c["code"])
        if hist is None or len(hist) < config.BOLL_WINDOW + config.HOLD_DAYS + 10:
            continue
        item = dict(c)
        item.update(evaluate(c, hist, p))
        out.append(item)

    # 用户口径：挑「历史胜率较高」的前 N 只。
    # 样本不足的胜率不可信，统一排到后面且不进入推送。
    out.sort(
        key=lambda x: (x["sample_ok"], x["win_rate"], x["avg_return"], x["signal_count"]),
        reverse=True,
    )
    eligible = [
        x for x in out if x["sample_ok"] and x["win_rate"] >= config.MIN_WIN_RATE
    ][: config.TOP_N]
    top_codes = {x["code"] for x in eligible}
    for i, item in enumerate(out, 1):
        item["rank"] = i
        item["is_top"] = item["code"] in top_codes

    rates = [x["win_rate"] for x in out if x["sample_ok"]] or [x["win_rate"] for x in out]
    summary = {
        "backtested": len(out),
        "avg_win_rate": round(float(np.mean(rates)), 4) if rates else 0.0,
        "top_n": len(eligible),
        "top_n_target": config.TOP_N,
        "hold_days": config.HOLD_DAYS,
        "backtest_years": config.BACKTEST_YEARS,
        "prior_win_rate": config.PRIOR_WIN_RATE,
        "prior_weight": config.PRIOR_WEIGHT,
        "min_signals": config.MIN_SIGNALS,
        "eligible_count": len([x for x in out if x["sample_ok"]]),
        "rule": f"信号出现后第 {config.HOLD_DAYS} 个交易日收盘价相对信号日收盘价 > 0 记为获胜",
        "smoothing": "贝叶斯平滑：(胜次 + 先验权重×先验胜率)/(样本数 + 先验权重)；样本不足不参与 TopN",
        "ranking": "按平滑后历史胜率降序，同胜率依次比较平均收益、信号次数",
    }
    return out, summary
