# -*- coding: utf-8 -*-
"""
全局配置：所有策略参数、数据源、推送与调度设置都集中在这里。
也可以通过同名环境变量覆盖，方便部署时不改代码。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- 路径
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"          # 历史日线本地缓存
RESULT_DIR = DATA_DIR / "results"       # 每次扫描结果 JSON
LOG_DIR = BASE_DIR / "logs"
for _d in (CACHE_DIR, RESULT_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv():
    """从项目根目录 .env 读取配置；已显式 export 的环境变量优先，不被覆盖。

    这样 `python main.py serve` 无需任何前置 export 即可带上 Webhook，
    又保留随时用环境变量临时覆盖的能力。.env 不会被 Web 服务暴露。
    """
    path = BASE_DIR / ".env"
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_dotenv()


def _env(key: str, default, cast=str):
    """读取环境变量并做类型转换。"""
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        if cast is bool:
            return raw.strip().lower() in ("1", "true", "yes", "y", "on")
        return cast(raw)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 数据源
# demo    : 内置演示数据（无需联网，用于验证流程 / 页面效果）
# akshare : 免费、免注册，东方财富数据源（默认，实盘用这个）
# tushare : 需 TUSHARE_TOKEN，按交易日批量取全市场，速度最快最稳定
# baostock: 免费，需逐只取，速度较慢
DATA_SOURCE = _env("STOCK_DATA_SOURCE", "akshare")
TUSHARE_TOKEN = _env("TUSHARE_TOKEN", "")
AUTO_FALLBACK_TO_DEMO = _env("AUTO_FALLBACK_TO_DEMO", True, bool)

# AkShare 全市场逐只拉历史会比较慢，首次全量约 15~40 分钟，之后走增量缓存
MAX_WORKERS = _env("MAX_WORKERS", 8, int)            # 并发线程数，怕被限流就调小
HIST_RETRY = _env("HIST_RETRY", 3, int)              # 单只重试次数
REQUEST_INTERVAL = _env("REQUEST_INTERVAL", 0.12, float)  # 每次请求间隔(秒)
HIST_START_DATE = _env("HIST_START_DATE", "")         # 留空=按 BACKTEST_YEARS 自动算
CACHE_MAX_AGE_DAYS = _env("CACHE_MAX_AGE_DAYS", 5, int)  # 缓存超过 N 天强制刷新
UNIVERSE_LIMIT = _env("UNIVERSE_LIMIT", 0, int)          # >0 时只扫描前 N 只，用于联调；0=全市场
SPOT_CACHE_MINUTES = _env("SPOT_CACHE_MINUTES", 20, int)  # 全市场快照缓存时效(分钟)

# ---------------------------------------------------------------- 调度
TIMEZONE = "Asia/Shanghai"
SCAN_HOUR = _env("SCAN_HOUR", 14, int)
SCAN_MINUTE = _env("SCAN_MINUTE", 30, int)
SCAN_WEEKDAYS = "mon-fri"                            # 再叠加交易日历判断
RUN_ON_START = _env("RUN_ON_START", False, bool)     # 服务启动时是否立即跑一次

# ---------------------------------------------------------------- 布林线与形态
BOLL_WINDOW = _env("BOLL_WINDOW", 20, int)           # 中轨 MA20
BOLL_STD_MULT = _env("BOLL_STD_MULT", 2.0, float)    # 上下轨 ±2 倍标准差
MIN_BREAK_DAYS = _env("MIN_BREAK_DAYS", 3, int)      # 至少连续 N 天收在下轨之下
MAX_CONT_LOOKBACK = _env("MAX_CONT_LOOKBACK", 10, int)
REQUIRE_STRICT_DECLINE = _env("REQUIRE_STRICT_DECLINE", True, bool)  # 破轨期间收盘价需逐日走低
MIN_DECLINE_PCT = _env("MIN_DECLINE_PCT", 2.0, float)  # 破轨区间累计跌幅下限(%)
MIN_YANG_PCT = _env("MIN_YANG_PCT", 0.5, float)      # 当日涨幅下限(%)，阳线确认
REQUIRE_CLOSE_ABOVE_LOWER = _env("REQUIRE_CLOSE_ABOVE_LOWER", True, bool)  # 收盘重新站上下轨
REQUIRE_LOW_TOUCH_LOWER = _env("REQUIRE_LOW_TOUCH_LOWER", False, bool)     # 盘中最低需触及下轨
REQUIRE_VOLUME_UP = _env("REQUIRE_VOLUME_UP", False, bool)                 # 放量确认
MAX_YANG_PCT = _env("MAX_YANG_PCT", 9.5, float)      # 排除已接近涨停、追不进的

# ---------------------------------------------------------------- 股票池过滤
EXCLUDE_PREFIXES = ("688", "689", "8", "4", "920")   # 剔除科创板、北交所
EXCLUDE_NAME_KEYWORDS = ("ST", "退", "*ST", "PT")    # 剔除 ST / 退市
MIN_AMOUNT = _env("MIN_AMOUNT", 3e7, float)          # 当日成交额下限(元)
MIN_PRICE = _env("MIN_PRICE", 2.0, float)
MAX_PRICE = _env("MAX_PRICE", 500.0, float)
MIN_LIST_DAYS = _env("MIN_LIST_DAYS", 60, int)       # 上市不足 N 个交易日剔除
MAX_CANDIDATES = _env("MAX_CANDIDATES", 300, int)    # 进入胜率回测的最大候选数

# ---------------------------------------------------------------- 历史胜率
BACKTEST_YEARS = _env("BACKTEST_YEARS", 3, int)      # 回测历史长度
HOLD_DAYS = _env("HOLD_DAYS", 10, int)               # 信号后持有 N 个交易日
MIN_SIGNALS = _env("MIN_SIGNALS", 2, int)            # 少于 N 次信号视为样本不足
PRIOR_WEIGHT = _env("PRIOR_WEIGHT", 4.0, float)      # 贝叶斯平滑强度
PRIOR_WIN_RATE = _env("PRIOR_WIN_RATE", 0.48, float)  # 先验胜率
MIN_WIN_RATE = _env("MIN_WIN_RATE", 0.0, float)      # 低于此胜率不进入 TopN

# ---------------------------------------------------------------- 推送
TOP_N = _env("TOP_N", 10, int)
WECOM_WEBHOOK = _env("WECOM_WEBHOOK", "")            # 企业微信群机器人 Webhook（推荐，最简单）
WECOM_KEYWORD = _env("WECOM_KEYWORD", "")            # 机器人安全设置里若设了关键词，填这里，程序会自动加进消息
WECOM_MENTION_MOBILES = _env("WECOM_MENTION_MOBILES", "")  # 逗号分隔，@指定成员
WECOM_MENTION_ALL = _env("WECOM_MENTION_ALL", False, bool)
# 可选：企业微信自建应用（未配置 Webhook 时生效）
WECOM_CORP_ID = _env("WECOM_CORP_ID", "")
WECOM_CORP_SECRET = _env("WECOM_CORP_SECRET", "")
WECOM_AGENT_ID = _env("WECOM_AGENT_ID", "")
WECOM_TO_USER = _env("WECOM_TO_USER", "@all")
WEB_BASE_URL = _env("WEB_BASE_URL", "http://127.0.0.1:8800")  # 推送消息里的网页链接

# ---------------------------------------------------------------- Web
WEB_HOST = _env("WEB_HOST", "0.0.0.0")
# 发布为在线应用时平台会注入 PORT，优先使用它
WEB_PORT = _env("PORT", _env("WEB_PORT", 8800, int), int)

# ---------------------------------------------------------------- 结果字段展示
DISPLAY_NAME = "布林下轨反转选股 · 14:30 扫描"
