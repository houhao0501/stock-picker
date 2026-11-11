# -*- coding: utf-8 -*-
"""
命令行入口。

  python main.py serve                 启动网页 + 交易日 14:30 定时调度
  python main.py serve --run-now      启动并立即执行一次扫描
  python main.py serve --no-scheduler 只启动网页，不开定时任务
  python main.py scan                 立即执行一次扫描（不启动网页）
  python main.py scan --source demo   用演示数据跑一次
  python main.py push-test            用最近一次结果测试企业微信推送
"""
from __future__ import annotations

import argparse
import sys

from core.utils import setup_logging


def cmd_serve(args):
    import config
    from app import app, create_app

    create_app(start_schedule=not args.no_scheduler, run_now=args.run_now)
    print(f"网页已启动: http://127.0.0.1:{args.port}  (数据源 {config.DATA_SOURCE})", flush=True)
    app.run(host=config.WEB_HOST, port=args.port, debug=False, threaded=True, use_reloader=False)


def cmd_scan(args):
    from core.pipeline import run_scan

    result = run_scan(reason="manual", source=args.source, do_push=not args.no_push)
    st = result["stats"]
    print("\n" + "=" * 66)
    print(f"扫描完成 {result['scan_time']}  数据源={result['source']}  耗时={result['elapsed_sec']}s")
    print(f"全市场 {st['universe_total']} → 过滤后 {st['after_filter']} → 命中 {st['signals']}")
    print(f"推送通道: {(result.get('push') or {}).get('channel', 'none')}")
    print("=" * 66)
    tops = [x for x in result["results"] if x["is_top"]]
    for i, x in enumerate(tops, 1):
        print(
            f"{i:2d}. {x['code']} {x['name']:<8} {x['price']:>8.2f} "
            f"{x['pct_chg']:>+6.2f}%  胜率 {x['win_rate'] * 100:5.1f}%  "
            f"均收益 {x['avg_return']:>+6.2f}%  信号 {x['signal_count']} 次  "
            f"破轨 {x['break_days']} 天"
        )
    if not tops:
        print("今日无符合形态的标的。")
    print(f"\n完整结果: data/results/latest.json   网页: python main.py serve")


def cmd_push_test(args):
    from core.notifier import test_push

    out = test_push()
    print(f"通道={out['channel']}  已配置={out['configured']}  成功={out['ok']}")
    print(f"结论：{out['detail']}")
    if out.get("hint"):
        print(f"提示：{out['hint']}")
    return 0 if out.get("ok") else 1


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="布林下轨反转选股系统")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="启动 Web 服务与定时调度")
    p_serve.add_argument("--run-now", action="store_true", help="启动时立即扫描一次")
    p_serve.add_argument("--no-scheduler", action="store_true", help="不启动定时调度")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.set_defaults(func=cmd_serve)

    p_scan = sub.add_parser("scan", help="立即执行一次扫描")
    p_scan.add_argument("--source", default=None, help="覆盖数据源 demo/akshare/tushare/baostock")
    p_scan.add_argument("--no-push", action="store_true", help="不推送企业微信")
    p_scan.set_defaults(func=cmd_scan)

    p_push = sub.add_parser("push-test", help="用最近结果测试推送")
    p_push.set_defaults(func=cmd_push_test)

    args = parser.parse_args()
    if args.cmd is None:
        args = parser.parse_args(["serve", "--run-now"])
    if getattr(args, "port", None) is None:
        import config

        args.port = config.WEB_PORT
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
