# -*- coding: utf-8 -*-
"""一次完整扫描的编排：行情 → 过滤 → 形态扫描 → 历史胜率 → 落盘 → 推送。"""
from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime

import config
from core.backtest import enrich
from core.data_source import load_market
from core.notifier import notify
from core.strategy import scan
from core.utils import atomic_write_json, now_cn, read_json, setup_logging

log = logging.getLogger(__name__)

_lock = threading.RLock()   # 可重入：run_scan 持锁期间会调用 _set/get_state
_state = {
    "running": False,
    "last_run": None,
    "last_error": None,
    "run_count": 0,
    "next_run": None,
    "progress": "",
}

LATEST = config.RESULT_DIR / "latest.json"
HISTORY_DIR = config.RESULT_DIR / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def get_state() -> dict:
    with _lock:
        return dict(_state)


def set_next_run(value: str | None) -> None:
    with _lock:
        _state["next_run"] = value


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def latest_result() -> dict | None:
    return read_json(LATEST)


def list_history(limit: int = 30) -> list[dict]:
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
    out = []
    for f in files:
        d = read_json(f, {})
        if not d:
            continue
        out.append(
            {
                "run_id": d.get("run_id", f.stem),
                "scan_time": d.get("scan_time", ""),
                "market_date": d.get("market_date", ""),
                "source": d.get("source", ""),
                "is_demo": d.get("is_demo", False),
                "signals": d.get("stats", {}).get("signals", 0),
                "trigger": d.get("trigger", ""),
            }
        )
    return out


def load_history(run_id: str) -> dict | None:
    safe = "".join(c for c in run_id if c.isalnum() or c in "_-")
    return read_json(HISTORY_DIR / f"{safe}.json")


def run_scan(reason: str = "manual", source: str | None = None, do_push: bool = True) -> dict:
    """同步执行一次扫描；同一时刻只允许一个扫描任务。"""
    setup_logging()
    if not _lock.acquire(blocking=False):
        log.warning("已有扫描任务在运行，跳过本次触发（%s）", reason)
        return {"skipped": True, "reason": "already_running"}
    try:
        t0 = datetime.now()
        _set(running=True, progress="加载行情数据", last_error=None)
        run_id = now_cn().strftime("%Y%m%d_%H%M%S")
        log.info("=== 开始扫描 %s，触发方式=%s，数据源=%s ===", run_id, reason, source or config.DATA_SOURCE)

        snap, histories, meta = load_market(source)
        is_demo = meta.get("source") == "demo"
        _set(progress="扫描全市场形态")

        params = None  # 使用 config 中的默认参数
        candidates, stats = scan(snap, histories, meta, params)
        _set(progress="计算历史胜率")
        ranked, bt_summary = enrich(candidates, histories, params)

        result = {
            "run_id": run_id,
            "scan_time": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
            "market_date": meta.get("market_date", ""),
            "source": meta.get("source", ""),
            "is_demo": is_demo,
            "source_note": meta.get("note", ""),
            "fallback_reason": meta.get("fallback_reason", ""),
            "trigger": reason,
            "elapsed_sec": 0,
            "stats": stats,
            "backtest_summary": bt_summary,
            "results": ranked,
            "params": {
                "boll_window": config.BOLL_WINDOW,
                "boll_std_mult": config.BOLL_STD_MULT,
                "min_break_days": config.MIN_BREAK_DAYS,
                "min_yang_pct": config.MIN_YANG_PCT,
                "min_decline_pct": config.MIN_DECLINE_PCT,
                "exclude_prefixes": list(config.EXCLUDE_PREFIXES),
                "min_amount": config.MIN_AMOUNT,
                "hold_days": config.HOLD_DAYS,
                "top_n": config.TOP_N,
                "scan_cron": f"交易日 {config.SCAN_HOUR:02d}:{config.SCAN_MINUTE:02d}",
            },
            "push": None,
        }

        if do_push:
            _set(progress="推送企业微信")
            try:
                result["push"] = notify(result)
            except Exception as exc:  # noqa: BLE001
                log.exception("推送异常: %s", exc)
                result["push"] = {"channel": "error", "ok": False, "detail": str(exc)[:200]}

        result["elapsed_sec"] = round((datetime.now() - t0).total_seconds(), 2)
        atomic_write_json(HISTORY_DIR / f"{run_id}.json", result)
        atomic_write_json(LATEST, result)
        _set(
            running=False,
            last_run=result["scan_time"],
            run_count=get_state()["run_count"] + 1,
            progress="",
        )
        log.info(
            "=== 扫描完成，耗时 %.1fs，命中 %d 只，Top%d 已推送（通道=%s）===",
            result["elapsed_sec"], stats["signals"], config.TOP_N,
            (result.get("push") or {}).get("channel", "none"),
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.error("扫描失败: %s\n%s", exc, traceback.format_exc())
        _set(running=False, last_error=str(exc)[:300], progress="")
        raise
    finally:
        _lock.release()


def run_scan_async(reason: str = "manual", source: str | None = None, do_push: bool = True) -> bool:
    """在后台线程触发一次扫描，立即返回。"""
    if get_state()["running"]:
        return False
    th = threading.Thread(
        target=run_scan, args=(reason, source, do_push), name="scan-worker", daemon=True
    )
    th.start()
    return True
