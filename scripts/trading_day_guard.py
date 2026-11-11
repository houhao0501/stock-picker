# -*- coding: utf-8 -*-
"""判断今天（北京时间）是否为 A 股交易日，供 GitHub Actions 跳过周末与节假日。

cron 只能排除周末，法定节假日必须由本脚本判断：
  1. 优先用 AkShare 的交易所交易日历（tool_trade_date_hist_sina）；
  2. 拉取失败时降级为「工作日 + data/holidays.txt 休市日兜底表」。

结果写入 $GITHUB_OUTPUT：trading=true/false、date、basis。
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parent.parent


def _today() -> date:
    return datetime.now(CN_TZ).date()


def _calendar_days() -> set[str] | None:
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        return {str(x) for x in df["trade_date"].astype(str).tolist()}
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::在线交易日历获取失败，降级为工作日近似：{exc}")
        return None


def _local_holidays() -> set[str]:
    p = ROOT / "data" / "holidays.txt"
    if not p.exists():
        return set()
    return {
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def main() -> int:
    day = _today()
    ds = day.isoformat()
    cal = _calendar_days()
    if cal is not None:
        trading = ds in cal
        basis = "A 股交易所日历（AkShare）"
    else:
        trading = day.weekday() < 5 and ds not in _local_holidays()
        basis = "工作日近似（日历不可用，请在 data/holidays.txt 维护休市日）"

    print(f"日期={ds} 星期={day.weekday() + 1} 交易日={trading} 判定依据={basis}")
    payload = f"trading={'true' if trading else 'false'}\ndate={ds}\nbasis={basis}\n"
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(payload)
    else:
        print("（本地运行，未写入 GITHUB_OUTPUT）\n" + payload)

    if not trading:
        print("::notice::非交易日，跳过扫描与推送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
