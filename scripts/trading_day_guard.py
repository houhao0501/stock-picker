# -*- coding: utf-8 -*-
"""控制 GitHub Actions 是否真正执行扫描。

当前系统为「按需手动筛选」模式：
  - schedule（定时）触发  -> trading=false，整条 job 全部跳过，不拉行情、
    不生成网页、不提交、不推送。彻底避免自动任务覆盖线上结果。
  - workflow_dispatch（手动）触发 -> trading=true，任何日期都放行，
    空仓想找票时随时可跑（非交易日拿到的是最近收盘快照）。
  - 本地运行 -> 保留原「A 股交易日历」判断，便于命令行单独使用。

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
    event = os.getenv("GITHUB_EVENT_NAME", "")

    if event == "schedule":
        # 手动模式：忽略一切定时触发，让下游所有步骤（含生成/提交/告警）全部跳过
        trading = False
        basis = "按需手动模式：忽略定时触发（schedule），未执行扫描"
    elif event == "workflow_dispatch":
        # 手动触发：不受交易日限制，随时可跑
        trading = True
        basis = "手动触发（workflow_dispatch），任何日期均放行"
    else:
        cal = _calendar_days()
        if cal is not None:
            trading = ds in cal
            basis = "A 股交易所日历（AkShare）"
        else:
            trading = day.weekday() < 5 and ds not in _local_holidays()
            basis = "工作日近似（日历不可用，请在 data/holidays.txt 维护休市日）"

    print(f"日期={ds} 星期={day.weekday() + 1} 事件={event or '本地'} 放行={trading} 判定依据={basis}")
    payload = f"trading={'true' if trading else 'false'}\ndate={ds}\nbasis={basis}\n"
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(payload)
    else:
        print("（本地运行，未写入 GITHUB_OUTPUT）\n" + payload)

    if not trading:
        print("::notice::非交易日或定时触发，跳过扫描与推送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
