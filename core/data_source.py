# -*- coding: utf-8 -*-
"""
行情数据源适配层。

统一输出两种数据：
  1) snapshot  : 全市场实时快照 DataFrame（14:30 时点）
                 字段 code/name/price/open/high/low/pre_close/pct_chg/amount/volume/...
  2) histories : {6 位代码: 历史日线 DataFrame}
                 字段 date/open/high/low/close/volume/amount（前复权，最后一根为「昨天」）

支持：akshare（默认）、tushare、baostock、demo。
真实源均带本地 CSV 缓存与增量更新：首次全市场较慢，之后每天只补最近数据。
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import config
from core.utils import atomic_write_json, load_trade_calendar, read_json, today_cn

log = logging.getLogger(__name__)

SNAP_COLS = [
    "code", "name", "price", "open", "high", "low", "pre_close",
    "pct_chg", "amount", "volume", "turnover", "market_cap",
]
HIST_COLS = ["date", "open", "high", "low", "close", "volume", "amount"]

_print_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_request = [0.0]


def _throttle():
    """简单的全局限速，避免把行情接口打挂。"""
    with _rate_lock:
        gap = time.time() - _last_request[0]
        if gap < config.REQUEST_INTERVAL:
            time.sleep(config.REQUEST_INTERVAL - gap)
        _last_request[0] = time.time()


def _recent_trade_day() -> str:
    """返回 <= 今天 的最近一个交易日（YYYYMMDD）。"""
    cal = load_trade_calendar()
    d = today_cn()
    for _ in range(15):
        ds = d.isoformat()
        if cal is None:
            if d.weekday() < 5 and ds not in _local_holidays():
                return d.strftime("%Y%m%d")
        elif ds in cal:
            return d.strftime("%Y%m%d")
        d -= timedelta(days=1)
    return today_cn().strftime("%Y%m%d")


def _local_holidays() -> set[str]:
    p = config.DATA_DIR / "holidays.txt"
    if not p.exists():
        return set()
    return {x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")}


def _hist_start() -> str:
    if config.HIST_START_DATE:
        return config.HIST_START_DATE.replace("-", "")
    d = today_cn() - timedelta(days=int(config.BACKTEST_YEARS * 365.5) + 60)
    return d.strftime("%Y%m%d")


def _norm_hist(df: pd.DataFrame) -> pd.DataFrame:
    """统一历史日线列名与类型，按日期升序去重。"""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    rename = {
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turnover",
        "trade_date": "date", "open": "open", "close": "close", "high": "high",
        "low": "low", "vol": "volume", "amount": "amount",
    }
    df = df.rename(columns=rename)
    if "date" not in df.columns:
        return pd.DataFrame(columns=HIST_COLS)
    df["date"] = df["date"].astype(str).str.replace("-", "", regex=False).str.slice(0, 8)
    df["date"] = df["date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:8]}" if len(x) == 8 else x)
    for c in ("open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = [c for c in HIST_COLS + ["pct_chg", "turnover"] if c in df.columns]
    df = df[keep].dropna(subset=["close"]).drop_duplicates(subset=["date"], keep="last")
    df = df.sort_values("date").reset_index(drop=True)
    for c in HIST_COLS[1:]:
        if c not in df.columns:
            df[c] = 0.0
    return df[HIST_COLS + [c for c in ("pct_chg", "turnover") if c in df.columns]]


class BaseSource:
    name = "base"

    def fetch(self) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict]:
        raise NotImplementedError

    # ------------------------------------------------------------ 缓存
    def _cache_dir(self) -> Path:
        d = config.CACHE_DIR / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _cache_path(self, code: str) -> Path:
        return self._cache_dir() / f"{code}.csv"

    def _read_cache(self, code: str) -> pd.DataFrame | None:
        p = self._cache_path(code)
        if not p.exists():
            return None
        try:
            df = pd.read_csv(p, dtype={"date": str})
            return _norm_hist(df) if len(df) else None
        except Exception:
            return None

    def _write_cache(self, code: str, df: pd.DataFrame) -> None:
        try:
            df.to_csv(self._cache_path(code), index=False, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.debug("缓存写入失败 %s: %s", code, exc)

    def _merge_cache(self, code: str, fresh: pd.DataFrame) -> pd.DataFrame:
        """增量合并：新数据覆盖最近一段，避免前复权造成的历史价格错位。"""
        old = self._read_cache(code)
        if old is None or len(old) == 0:
            merged = fresh
        else:
            cut = fresh["date"].min() if len(fresh) else old["date"].max()
            merged = pd.concat([old[old["date"] < cut], fresh], ignore_index=True)
            merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
        self._write_cache(code, merged)
        return merged

    def _cache_is_stale(self, code: str, end_iso: str) -> bool:
        old = self._read_cache(code)
        if old is None or len(old) < config.BOLL_WINDOW + config.MIN_BREAK_DAYS + 5:
            return True
        try:
            last = date.fromisoformat(str(old["date"].iloc[-1]))
            end = date.fromisoformat(end_iso)
            return (end - last).days > config.CACHE_MAX_AGE_DAYS
        except Exception:
            return True

    def _batch_histories(self, codes: list[str], fetch_one) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        total = len(codes)
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, config.MAX_WORKERS)) as pool:
            futs = {pool.submit(fetch_one, c): c for c in codes}
            for fut in as_completed(futs):
                c = futs[fut]
                done += 1
                try:
                    df = fut.result()
                    if df is not None and len(df) >= config.BOLL_WINDOW + config.MIN_BREAK_DAYS + 5:
                        out[c] = df
                except Exception as exc:  # noqa: BLE001
                    log.debug("历史数据获取失败 %s: %s", c, exc)
                if done % 200 == 0 or done == total:
                    log.info("历史日线进度 %d/%d，成功 %d", done, total, len(out))
        return out


def preselect_codes(snap: pd.DataFrame) -> list[str]:
    """零历史成本的前置粗筛：只用全市场快照字段，先剔掉绝不可能命中信号的股票。

    设计原则：只做「最终判定的宽松必要条件」，宁可多留、绝不误杀；完整判定
    仍由 strategy.apply_filters + check_reversal 执行。本函数唯一作用是减少
    需要逐只拉取 3 年历史的股票数量——这正是耗时与东方财富限流的根源。

    依据 2026-11-11 真实全市场运行的过滤明细（共 5928 只）：
      科创板/北交所 987、ST/退市 456、价格异常 112、成交额不足/停牌 555，
      合计 2110 只原本用快照就能剔除，却各自拉了 3 年历史。
    另加「收阳 + 涨幅区间」：对应 check_reversal_fast 的前两条硬判定，
    用快照的 price / open / pct_chg 即可判断，零额外请求再砍掉当日下跌的股票。
    涨幅边界刻意放宽，避免前复权与快照昨收在除权日口径不一致导致漏信号。
    """
    lo = config.MIN_YANG_PCT - 1.0
    hi = config.MAX_YANG_PCT + 1.0
    prefixes = tuple(config.EXCLUDE_PREFIXES)
    kw = [k.upper() for k in config.EXCLUDE_NAME_KEYWORDS]
    out: list[str] = []
    for row in snap.to_dict("records"):
        code = str(row.get("code", "")).zfill(6)
        if code.startswith(prefixes):
            continue
        name = str(row.get("name", "") or "")
        if any(k in name.upper() for k in kw):
            continue
        try:
            price = float(row.get("price") or 0)
            pre_close = float(row.get("pre_close") or 0)
            amount = float(row.get("amount") or 0)
            volume = float(row.get("volume") or 0)
            open_ = float(row.get("open") or 0)
        except (TypeError, ValueError):
            continue
        if price < config.MIN_PRICE or price > config.MAX_PRICE or pre_close <= 0:
            continue
        if amount < config.MIN_AMOUNT or volume <= 0:
            continue
        # 收阳：与 check_reversal_fast 的 close[i] > open[i] 同源同值
        if price <= open_:
            continue
        # 涨幅区间：宽松超集；字段异常时保留，交给后续完整判定
        try:
            pct = float(row.get("pct_chg"))
            if pct == pct and abs(pct) != float("inf") and not (lo <= pct <= hi):
                continue
        except (TypeError, ValueError):
            pass
        out.append(code)
    return out


# ---------------------------------------------------------------- AkShare
class AkShareSource(BaseSource):
    name = "akshare"

    def _spot(self) -> pd.DataFrame:
        import akshare as ak

        cache_file = config.DATA_DIR / "spot_cache.json"
        cached = read_json(cache_file)
        if cached and (time.time() - cached.get("ts", 0)) < config.SPOT_CACHE_MINUTES * 60:
            return pd.DataFrame(cached["rows"])
        log.info("拉取全市场实时快照 ...")
        raw = ak.stock_zh_a_spot_em()
        mp = {
            "代码": "code", "名称": "name", "最新价": "price", "今开": "open", "最高": "high",
            "最低": "low", "昨收": "pre_close", "涨跌幅": "pct_chg", "成交额": "amount",
            "成交量": "volume", "换手率": "turnover", "总市值": "market_cap",
        }
        df = raw.rename(columns=mp)
        for c in SNAP_COLS:
            if c not in df.columns:
                df[c] = 0.0
        df = df[SNAP_COLS].copy()
        df["code"] = df["code"].astype(str).str.zfill(6)
        for c in SNAP_COLS[2:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        atomic_write_json(cache_file, {"ts": time.time(), "rows": df.to_dict("records")})
        return df

    def _hist_one(self, code: str, end_yyyymmdd: str) -> pd.DataFrame | None:
        import akshare as ak

        end_iso = f"{end_yyyymmdd[:4]}-{end_yyyymmdd[4:6]}-{end_yyyymmdd[6:8]}"
        stale = self._cache_is_stale(code, end_iso)
        old = None if stale else self._read_cache(code)
        if old is not None and len(old):
            start_dt = date.fromisoformat(str(old["date"].iloc[-1])) - timedelta(days=12)
            start = start_dt.strftime("%Y%m%d")
        else:
            start = _hist_start()

        raw = None
        for attempt in range(config.HIST_RETRY):
            try:
                _throttle()
                raw = ak.stock_zh_a_hist(
                    symbol=code, period="daily", start_date=start,
                    end_date=end_yyyymmdd, adjust="qfq",
                )
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == config.HIST_RETRY - 1:
                    log.debug("akshare %s 重试耗尽: %s", code, exc)
                    return old
                time.sleep(0.6 * (attempt + 1) + random.random() * 0.4)
        if raw is None or len(raw) == 0:
            return old
        fresh = _norm_hist(raw)
        # 当日盘中 K 线未收盘，丢弃，改由 14:30 快照构造临时 K 线
        fresh = fresh[fresh["date"] < end_iso]
        if len(fresh) == 0:
            return old
        return self._merge_cache(code, fresh)

    def fetch(self) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict]:
        snap = self._spot()
        end = _recent_trade_day()
        # 前置粗筛：先用零成本的快照条件缩小股票池，再逐只拉 3 年历史。
        # 被剔除的股票在 strategy.apply_filters / check_reversal 里本来也一定不命中，
        # 因此结果完全不变，只是省下大量无效请求（同时缓解东方财富限流）。
        codes = preselect_codes(snap)
        after_prefilter = len(codes)
        if config.UNIVERSE_LIMIT > 0:
            codes = codes[: config.UNIVERSE_LIMIT]
        log.info("AkShare 全市场 %d 只，前置粗筛后 %d 只需拉历史（并发 %d）",
                 len(snap), len(codes), config.MAX_WORKERS)
        hists = self._batch_histories(codes, lambda c: self._hist_one(c, end))
        meta = {
            "source": "akshare", "market_date": f"{end[:4]}-{end[4:6]}-{end[6:8]}",
            "universe_total": len(snap), "preselected": after_prefilter,
            "history_loaded": len(hists),
            "note": "实时行情来自东方财富，历史日线为前复权，已使用本地缓存增量更新",
        }
        return snap, hists, meta


# ---------------------------------------------------------------- Tushare
class TushareSource(BaseSource):
    name = "tushare"

    def __init__(self):
        if not config.TUSHARE_TOKEN:
            raise RuntimeError("使用 tushare 需要设置环境变量 TUSHARE_TOKEN")
        import tushare as ts

        self.pro = ts.pro_api(config.TUSHARE_TOKEN)
        self.ts = ts

    def _spot(self) -> pd.DataFrame:
        end = _recent_trade_day()
        basic = self.pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,market")
        daily = self.pro.daily(trade_date=end, fields="ts_code,open,high,low,close,pre_close,pct_chg,amount,vol")
        if daily is None or len(daily) == 0:
            cal = self.pro.trade_cal(exchange="SSE", end_date=end, is_open="1", fields="cal_date")
            end = str(cal["cal_date"].iloc[0])
            daily = self.pro.daily(trade_date=end, fields="ts_code,open,high,low,close,pre_close,pct_chg,amount,vol")
        df = daily.merge(basic, on="ts_code", how="left")
        out = pd.DataFrame({
            "code": df["symbol"].astype(str).str.zfill(6),
            "name": df["name"].fillna(""),
            "price": pd.to_numeric(df["close"], errors="coerce"),
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "high": pd.to_numeric(df["high"], errors="coerce"),
            "low": pd.to_numeric(df["low"], errors="coerce"),
            "pre_close": pd.to_numeric(df["pre_close"], errors="coerce"),
            "pct_chg": pd.to_numeric(df["pct_chg"], errors="coerce"),
            "volume": pd.to_numeric(df["vol"], errors="coerce") * 100,
            "amount": pd.to_numeric(df["amount"], errors="coerce") * 1000,
            "turnover": 0.0, "market_cap": 0.0,
        })
        # Tushare daily 是收盘数据；14:30 场景可改用实时行情接口，这里保持结构一致
        return out[SNAP_COLS]

    def _hist_one(self, code: str, end_yyyymmdd: str) -> pd.DataFrame | None:
        suffix = "SH" if code.startswith(("6", "9")) else ("BJ" if code.startswith(("8", "4")) else "SZ")
        ts_code = f"{code}.{suffix}"
        end_iso = f"{end_yyyymmdd[:4]}-{end_yyyymmdd[4:6]}-{end_yyyymmdd[6:8]}"
        old = None if self._cache_is_stale(code, end_iso) else self._read_cache(code)
        start = (date.fromisoformat(str(old["date"].iloc[-1])) - timedelta(days=12)).strftime("%Y%m%d") if old is not None and len(old) else _hist_start()
        for attempt in range(config.HIST_RETRY):
            try:
                _throttle()
                df = self.ts.pro_bar(ts_code=ts_code, adj="qfq", start_date=start, end_date=end_yyyymmdd,
                                     factors=["tor", "vr"], adjfactor=True)
                if df is None or len(df) == 0:
                    return old
                norm = _norm_hist(df.rename(columns={"vol": "volume"}))
                norm = norm[norm["date"] < end_iso]
                return self._merge_cache(code, norm) if len(norm) else old
            except Exception as exc:  # noqa: BLE001
                if attempt == config.HIST_RETRY - 1:
                    log.debug("tushare %s 失败: %s", code, exc)
                    return old
                time.sleep(0.5 * (attempt + 1))
        return old

    def fetch(self) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict]:
        snap = self._spot()
        end = _recent_trade_day()
        codes = snap["code"].astype(str).tolist()
        if config.UNIVERSE_LIMIT > 0:
            codes = codes[: config.UNIVERSE_LIMIT]
        hists = self._batch_histories(codes, lambda c: self._hist_one(c, end))
        meta = {"source": "tushare", "market_date": f"{end[:4]}-{end[4:6]}-{end[6:8]}",
                "universe_total": len(snap), "history_loaded": len(hists),
                "note": "Tushare Pro 日线（前复权）；14:30 实时价建议配合行情接口替换快照"}
        return snap, hists, meta


# ---------------------------------------------------------------- Baostock
class BaoStockSource(BaseSource):
    """历史日线用 baostock，实时快照仍用 AkShare（baostock 无全市场实时快照）。"""
    name = "baostock"

    def __init__(self):
        import baostock as bs

        self.bs = bs
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")

    @staticmethod
    def _bs_code(code: str) -> str:
        return ("sh." if code.startswith(("6", "9")) else "bj." if code.startswith(("8", "4")) else "sz.") + code

    def _hist_one(self, code: str, end_yyyymmdd: str) -> pd.DataFrame | None:
        end_iso = f"{end_yyyymmdd[:4]}-{end_yyyymmdd[4:6]}-{end_yyyymmdd[6:8]}"
        old = None if self._cache_is_stale(code, end_iso) else self._read_cache(code)
        start = (date.fromisoformat(str(old["date"].iloc[-1])) - timedelta(days=12)).isoformat() if old is not None and len(old) else (today_cn() - timedelta(days=int(config.BACKTEST_YEARS * 365.5) + 60)).isoformat()
        fields = "date,open,high,low,close,volume,amount"
        for attempt in range(config.HIST_RETRY):
            try:
                _throttle()
                rs = self.bs.query_history_k_data_plus(
                    self._bs_code(code), fields, start_date=start, end_date=end_iso,
                    frequency="d", adjustflag="2",
                )
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                if not rows:
                    return old
                raw = pd.DataFrame(rows, columns=fields.split(","))
                norm = _norm_hist(raw)
                norm = norm[norm["date"] < end_iso]
                return self._merge_cache(code, norm) if len(norm) else old
            except Exception as exc:  # noqa: BLE001
                if attempt == config.HIST_RETRY - 1:
                    log.debug("baostock %s 失败: %s", code, exc)
                    return old
                time.sleep(0.6 * (attempt + 1))
        return old

    def fetch(self) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict]:
        snap = AkShareSource()._spot()
        end = _recent_trade_day()
        codes = snap["code"].astype(str).tolist()
        if config.UNIVERSE_LIMIT > 0:
            codes = codes[: config.UNIVERSE_LIMIT]
        hists = self._batch_histories(codes, lambda c: self._hist_one(c, end))
        try:
            self.bs.logout()
        except Exception:
            pass
        meta = {"source": "baostock", "market_date": f"{end[:4]}-{end[4:6]}-{end[6:8]}",
                "universe_total": len(snap), "history_loaded": len(hists),
                "note": "历史日线来自 baostock，实时快照来自东方财富"}
        return snap, hists, meta


# ---------------------------------------------------------------- Demo
class DemoSource(BaseSource):
    name = "demo"

    def fetch(self) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict]:
        from core.demo_data import generate

        snap, hists, meta = generate()
        meta["note"] = "演示数据（固定随机种子），仅用于验证流程与页面；设置 STOCK_DATA_SOURCE=akshare 使用真实行情"
        return snap, hists, meta


_SOURCES = {
    "demo": DemoSource,
    "akshare": AkShareSource,
    "tushare": TushareSource,
    "baostock": BaoStockSource,
}


def get_source(name: str | None = None) -> BaseSource:
    key = (name or config.DATA_SOURCE).strip().lower()
    if key not in _SOURCES:
        raise ValueError(f"未知数据源 {key}，可选: {list(_SOURCES)}")
    return _SOURCES[key]()


def load_market(name: str | None = None) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict]:
    """加载行情；真实源失败时按配置自动降级到演示数据。"""
    key = (name or config.DATA_SOURCE).strip().lower()
    try:
        return get_source(key).fetch()
    except Exception as exc:  # noqa: BLE001
        log.exception("数据源 %s 获取失败: %s", key, exc)
        if key != "demo" and config.AUTO_FALLBACK_TO_DEMO:
            log.warning("自动降级到演示数据，结果页会标注 DEMO，请勿用于实盘决策")
            snap, hists, meta = DemoSource().fetch()
            meta["fallback_from"] = key
            meta["fallback_reason"] = str(exc)[:200]
            return snap, hists, meta
        raise
