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

akshare 源内置多级快照兜底：东方财富 push2 → 新浪分页 → 腾讯批量 → 上次成功快照。
东财 push2 在部分机房 IP 段会被长期断连；新浪 ak.stock_zh_a_spot() 内部循环
60+ 页却共用一个 60 秒超时，任意一页抖动就会整轮 ReadTimeout。因此这里改为
自行分页、逐页短超时独立重试，并补上腾讯批量通道与跨运行缓存降级。
策略、参数、结果口径均不变，仅按通道隔离历史缓存目录，避免东财（成交量=手）
与新浪（成交量=股）混用。

快照缓存写入 data/cache/spot_last.json —— GitHub Actions 只持久化 data/cache，
写在这里才能跨运行复用；降级时页面会明确标注「快照滞后」。
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
from core.utils import atomic_write_json, load_trade_calendar, now_cn, read_json, today_cn

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


def _clean_sina_code(value) -> str:
    """新浪快照代码形如 sh600000 / sz000001 / bj920000，清洗成纯 6 位数字。

    股票池过滤与前置粗筛都按纯 6 位代码做前缀判断（688/689/8/4/920），
    带前缀会让科创板/北交所漏进选股池，因此进入统一数据契约前必须先清洗。
    """
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return (digits[-6:] if len(digits) >= 6 else digits).zfill(6)


def _sina_symbol(code: str) -> str:
    """纯 6 位代码 → 新浪日线 symbol（sh/sz/bj 前缀）。"""
    if code.startswith("920") or code.startswith(("8", "4")):
        return "bj" + code
    if code.startswith(("6", "9")):
        return "sh" + code
    return "sz" + code


# ---------------------------------------------------------------- 快照兜底
SINA_NODE_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
TENCENT_QUOTE_URL = "http://qt.gtimg.cn/q="
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}
SPOT_PAGE_SIZE = 80          # 新浪单页条数
SPOT_PAGE_TIMEOUT = 8        # 单页/单批超时(秒)，替代原来整轮 60 秒
SPOT_PAGE_RETRY = 3          # 单页/单批重试次数
SPOT_MIN_ROWS = 3000         # 少于此数视为该通道失败
TENCENT_BATCH = 60           # 腾讯单次批量查询只数


def _snap_last_path() -> Path:
    """跨运行快照缓存。必须位于 data/cache —— Actions 只持久化这个目录。"""
    return config.CACHE_DIR / "spot_last.json"


def _normalize_snap(df: pd.DataFrame) -> pd.DataFrame:
    """统一快照列、类型、代码格式；缺列补 0。"""
    df = df.copy()
    for c in SNAP_COLS:
        if c not in df.columns:
            df[c] = 0.0
    df = df[SNAP_COLS].copy()
    df["code"] = df["code"].astype(str).map(_clean_sina_code)
    df["name"] = df["name"].astype(str)
    for c in SNAP_COLS[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.drop_duplicates(subset=["code"], keep="last").reset_index(drop=True)


def _read_last_snap() -> tuple[pd.DataFrame, dict] | None:
    c = read_json(_snap_last_path())
    if not c or not c.get("rows"):
        return None
    try:
        return _normalize_snap(pd.DataFrame(c["rows"])), c
    except Exception:  # noqa: BLE001
        return None


def _save_last_snap(df: pd.DataFrame, channel: str) -> None:
    try:
        atomic_write_json(_snap_last_path(), {
            "ts": time.time(),
            "now": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
            "channel": channel,
            "rows": df.to_dict("records"),
        })
    except Exception as exc:  # noqa: BLE001
        log.warning("快照缓存写入失败（降级能力可能失效）: %s", exc)


def _sina_pages() -> pd.DataFrame:
    """新浪全市场快照：分页拉取，逐页短超时 + 独立重试。

    不用 ak.stock_zh_a_spot()：它内部循环 60+ 页且共用 60 秒超时，
    任意一页抖动会让整轮 ReadTimeout（线上 #11 的失败原因）。
    坏页只跳过、不拖垮整轮；只要总量够就继续用。
    """
    import requests

    sess = requests.Session()
    out: list[dict] = []
    failed = 0
    for page in range(1, 200):
        raw = None
        for attempt in range(SPOT_PAGE_RETRY):
            try:
                r = sess.get(SINA_NODE_URL, params={
                    "page": page, "num": SPOT_PAGE_SIZE, "sort": "symbol",
                    "asc": 1, "node": "hs_a",
                }, headers=_HTTP_HEADERS, timeout=SPOT_PAGE_TIMEOUT)
                r.raise_for_status()
                raw = r.json()
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == SPOT_PAGE_RETRY - 1:
                    failed += 1
                    log.warning("新浪快照第 %d 页失败，跳过: %s", page, str(exc)[:90])
                else:
                    time.sleep(0.4 * (attempt + 1))
        if raw is None:
            continue
        if not isinstance(raw, list) or len(raw) == 0:
            break
        for it in raw:
            out.append({
                "code": it.get("code"), "name": it.get("name"),
                "price": it.get("trade"), "open": it.get("open"),
                "high": it.get("high"), "low": it.get("low"),
                "pre_close": it.get("settlement"), "pct_chg": it.get("changepercent"),
                "amount": it.get("amount"), "volume": it.get("volume"),
                "turnover": it.get("turnoverratio"), "market_cap": 0.0,
            })
        if len(raw) < SPOT_PAGE_SIZE:
            break
    if failed:
        log.warning("新浪快照共 %d 页拉取失败（已跳过）", failed)
    df = _normalize_snap(pd.DataFrame(out))
    if len(df) < SPOT_MIN_ROWS:
        raise RuntimeError(f"新浪分页仅取得 {len(df)} 只，视为通道失败")
    return df


def _tencent_symbol(code: str) -> str:
    if code.startswith("920") or code.startswith(("8", "4")):
        return "bj" + code
    if code.startswith(("6", "9")):
        return "sh" + code
    return "sz" + code


def _tencent_snap(codes: list[str]) -> pd.DataFrame:
    """腾讯批量行情第三通道：股票列表来自上次成功快照 / 代码表。"""
    import requests

    sess = requests.Session()
    out: list[dict] = []
    for i in range(0, len(codes), TENCENT_BATCH):
        batch = codes[i:i + TENCENT_BATCH]
        url = TENCENT_QUOTE_URL + ",".join(_tencent_symbol(c) for c in batch)
        txt = ""
        for attempt in range(SPOT_PAGE_RETRY):
            try:
                r = sess.get(url, headers=_HTTP_HEADERS, timeout=SPOT_PAGE_TIMEOUT)
                r.raise_for_status()
                txt = r.content.decode("gbk", "replace")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == SPOT_PAGE_RETRY - 1:
                    log.warning("腾讯行情第 %d 批失败，跳过: %s",
                                i // TENCENT_BATCH + 1, str(exc)[:80])
                else:
                    time.sleep(0.4 * (attempt + 1))
        for line in txt.split(";"):
            if '="' not in line:
                continue
            f = line.split('="', 1)[1].rstrip('"').split("~")
            if len(f) < 39:
                continue
            try:
                out.append({
                    "code": f[2], "name": f[1],
                    "price": f[3], "open": f[5], "high": f[33], "low": f[34],
                    "pre_close": f[4], "pct_chg": f[32],
                    "volume": float(f[6]) * 100,       # 手 → 股
                    "amount": float(f[37]) * 10000,    # 万元 → 元
                    "turnover": f[38], "market_cap": 0.0,
                })
            except (ValueError, IndexError):
                continue
    df = _normalize_snap(pd.DataFrame(out))
    # 代码表本身不完整时按比例判定，避免「请求 597 只、全回来了」被误判为失败
    need = min(SPOT_MIN_ROWS, max(100, int(len(codes) * 0.8)))
    if len(df) < need:
        raise RuntimeError(f"腾讯通道仅取得 {len(df)}/{len(codes)} 只（需 {need}），视为通道失败")
    return df


def _fallback_codes(last: tuple | None) -> list[str]:
    """腾讯通道需要的代码表，三级来源保证冷启动也可用。

    1) 上次成功快照；2) 历史日线缓存的 CSV 文件名（Actions 会持久化 data/cache）；
    3) AkShare 代码表（东财，可能同样断连，仅作最后尝试）。
    """
    if last is not None:
        return [str(c).zfill(6) for c in last[0]["code"].astype(str).tolist() if c]
    cached: set[str] = set()
    for sub in ("sina", "akshare"):
        d = config.CACHE_DIR / sub
        if d.is_dir():
            cached.update(p.stem for p in d.glob("*.csv") if p.stem.isdigit())
    if len(cached) >= SPOT_MIN_ROWS:
        log.info("腾讯通道代码表来自历史日线缓存：%d 只", len(cached))
        return sorted(cached)
    try:
        import akshare as ak

        info = ak.stock_info_a_code_name()
        return [str(x).zfill(6) for x in info["code"].astype(str).tolist()]
    except Exception as exc:  # noqa: BLE001
        log.warning("无法获取股票代码表，腾讯通道不可用: %s", str(exc)[:100])
        return sorted(cached)


# ---------------------------------------------------------------- AkShare
class AkShareSource(BaseSource):
    """快照四级兜底：东财 → 新浪分页 → 腾讯批量 → 上次成功快照（标注滞后）。

    _channel 只决定「历史日线」走哪个通道（eastmoney / sina），与快照通道解耦：
    腾讯只提供快照，历史日线仍走新浪，避免缓存单位混用。
    输出仍统一为 SNAP_COLS / HIST_COLS，source 仍标记为 akshare
    （前端演示数据判定依赖它），仅额外记录 channel / snapshot_*。
    """
    name = "akshare"

    def __init__(self):
        self._channel = "eastmoney"   # 历史日线通道："eastmoney" | "sina"
        self._snap_channel = ""       # 本次快照实际通道
        self._snap_stale = False      # 是否降级复用了旧快照
        self._snap_saved_at = ""      # 旧快照的保存时刻

    def _cache_dir(self) -> Path:
        # 通道隔离：新浪用独立缓存目录，避免与东财前复权/成交量单位混用
        sub = self.name if self._channel == "eastmoney" else "sina"
        d = config.CACHE_DIR / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _spot(self) -> pd.DataFrame:
        """四级兜底取全市场快照，任一通道成功即写入跨运行缓存。

        设计目标：绝不让单点网络抖动导致整轮扫描失败、页面停更、推失败告警。
        每个通道自身带重试；全部失败才复用上次成功快照并标记「快照滞后」。
        """
        last = _read_last_snap()
        for label, fn, hist_channel in (
            ("eastmoney", self._spot_em, "eastmoney"),
            ("sina", self._spot_sina, "sina"),
        ):
            try:
                df = fn()
                if df is None or len(df) < SPOT_MIN_ROWS:
                    raise RuntimeError(f"{label} 快照仅 {0 if df is None else len(df)} 只")
                self._channel = hist_channel
                self._snap_channel = label
                self._snap_stale = False
                _save_last_snap(df, label)
                log.info("全市场快照通道=%s，%d 只", label, len(df))
                return df
            except Exception as exc:  # noqa: BLE001
                log.warning("%s 快照不可用，继续下一通道: %s", label, str(exc)[:150])

        # 第三通道：腾讯批量（代码表来自上次成功快照 / AkShare 代码表）
        codes = _fallback_codes(last)
        if codes:
            try:
                df = _tencent_snap(codes)
                self._channel = "sina"   # 腾讯只给快照，历史日线仍走新浪
                self._snap_channel = "tencent"
                self._snap_stale = False
                _save_last_snap(df, "tencent")
                log.info("全市场快照通道=腾讯，%d 只", len(df))
                return df
            except Exception as exc:  # noqa: BLE001
                log.warning("腾讯快照不可用，降级复用缓存: %s", str(exc)[:150])

        # 最后兜底：复用上次成功快照（不限时效），页面标注滞后
        if last is not None:
            df, c = last
            self._channel = "eastmoney" if c.get("channel") == "eastmoney" else "sina"
            self._snap_stale = True
            self._snap_channel = "cached:" + str(c.get("channel", "?"))
            self._snap_saved_at = str(c.get("now", ""))
            log.warning("三通道均失败，降级复用 %s 的快照（%d 只），页面将标注滞后",
                        self._snap_saved_at, len(df))
            return df
        raise RuntimeError("东财/新浪/腾讯三通道均失败，且无可复用的历史快照")

    def _spot_em(self) -> pd.DataFrame:
        import akshare as ak

        cache_file = config.DATA_DIR / "spot_cache.json"
        cached = read_json(cache_file)
        if (cached and cached.get("channel") == "eastmoney"
                and (time.time() - cached.get("ts", 0)) < config.SPOT_CACHE_MINUTES * 60):
            return pd.DataFrame(cached["rows"])
        log.info("拉取全市场实时快照（东方财富）...")
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
        atomic_write_json(cache_file, {"ts": time.time(), "channel": "eastmoney",
                                       "rows": df.to_dict("records")})
        return df

    def _spot_sina(self) -> pd.DataFrame:
        # 同一轮内的短缓存（跨运行缓存由 _save_last_snap 负责）
        cache_file = config.DATA_DIR / "spot_cache.json"
        cached = read_json(cache_file)
        if (cached and cached.get("channel") == "sina"
                and (time.time() - cached.get("ts", 0)) < config.SPOT_CACHE_MINUTES * 60):
            return _normalize_snap(pd.DataFrame(cached["rows"]))
        log.info("拉取全市场实时快照（新浪·分页逐页重试）...")
        df = _sina_pages()
        atomic_write_json(cache_file, {"ts": time.time(), "channel": "sina",
                                       "rows": df.to_dict("records")})
        return df

    def _hist_one(self, code: str, end_yyyymmdd: str) -> pd.DataFrame | None:
        if self._channel == "sina":
            return self._hist_one_sina(code, end_yyyymmdd)
        return self._hist_one_em(code, end_yyyymmdd)

    def _hist_one_sina(self, code: str, end_yyyymmdd: str) -> pd.DataFrame | None:
        import akshare as ak

        end_iso = f"{end_yyyymmdd[:4]}-{end_yyyymmdd[4:6]}-{end_yyyymmdd[6:8]}"
        stale = self._cache_is_stale(code, end_iso)
        old = None if stale else self._read_cache(code)
        if old is not None and len(old):
            start_dt = date.fromisoformat(str(old["date"].iloc[-1])) - timedelta(days=12)
            start = start_dt.strftime("%Y%m%d")
        else:
            start = _hist_start()
        start_iso = f"{start[:4]}-{start[4:6]}-{start[6:8]}"

        raw = None
        for attempt in range(config.HIST_RETRY):
            try:
                _throttle()
                raw = ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust="qfq")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == config.HIST_RETRY - 1:
                    log.debug("sina %s 重试耗尽: %s", code, exc)
                    return old
                time.sleep(0.6 * (attempt + 1) + random.random() * 0.4)
        if raw is None or len(raw) == 0:
            return old
        fresh = _norm_hist(raw)
        # 新浪返回上市以来全历史：按回看窗口裁剪，并丢弃当日未收盘 K 线
        fresh = fresh[(fresh["date"] >= start_iso) & (fresh["date"] < end_iso)]
        if len(fresh) == 0:
            return old
        return self._merge_cache(code, fresh)

    def _hist_one_em(self, code: str, end_yyyymmdd: str) -> pd.DataFrame | None:
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
        log.info("AkShare 全市场 %d 只，前置粗筛后 %d 只需拉历史（通道=%s，并发 %d）",
                 len(snap), len(codes), self._channel, config.MAX_WORKERS)
        hists = self._batch_histories(codes, lambda c: self._hist_one(c, end))
        snap_origin = {
            "eastmoney": "实时快照来自东方财富",
            "sina": "实时快照来自新浪（分页逐页重试）",
            "tencent": "实时快照来自腾讯行情（东财/新浪均不可用，已自动切换）",
        }.get(self._snap_channel, "实时快照来自上次缓存（非当日实时）")
        hist_origin = (
            "历史日线来自东方财富（前复权，本地缓存增量更新）"
            if self._channel == "eastmoney" else
            "历史日线来自新浪（前复权，独立缓存目录，增量更新）"
        )
        channel_note = f"{snap_origin}；{hist_origin}"
        if self._snap_stale:
            channel_note += (
                f"；【快照滞后】三通道均不可用，复用 {self._snap_saved_at} 的缓存价，"
                "本次价格非当日实时，结果仅供复盘、请勿据此下单"
            )
        meta = {
            "source": "akshare", "market_date": f"{end[:4]}-{end[4:6]}-{end[6:8]}",
            "universe_total": len(snap), "preselected": after_prefilter,
            "history_loaded": len(hists), "channel": self._channel,
            "snapshot_channel": self._snap_channel,
            "snapshot_stale": self._snap_stale,
            "snapshot_saved_at": self._snap_saved_at,
            "note": channel_note,
        }
        if self._snap_stale:
            log.warning("本次使用滞后快照，已跳过推送并在页面标注（%s）", self._snap_saved_at)
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
