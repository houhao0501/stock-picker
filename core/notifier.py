# -*- coding: utf-8 -*-
"""企业微信推送：群机器人 Webhook / 自建应用；未配置时降级为本地预览文件。"""
from __future__ import annotations

import logging

import requests

import config
from core.utils import now_cn

log = logging.getLogger(__name__)

# 常见错误码的排查建议（HTTP 200 不代表发成功，必须看 errcode）
_ERR_HINT = {
    93000: "Webhook 无效或机器人已被移除，请回企业微信重新复制地址",
    45009: "触发频率限制（每个机器人 20 条/分钟），稍后再试",
    40058: "参数不合法，常见原因是消息没带机器人设置的关键词（填 WECOM_KEYWORD）",
    40001: "凭据无效，检查 Secret / Webhook key",
    60020: "调用方 IP 不在可信 IP 白名单里（自建应用需在应用详情页配置企业可信 IP）",
}


def _with_keyword(text: str) -> str:
    """机器人若开启了「关键词」安全设置，消息里必须包含该词，否则会被静默拒收。"""
    kw = (config.WECOM_KEYWORD or "").strip()
    if kw and kw not in text:
        return f"{kw}\n\n{text}"
    return text


# ------------------------------------------------------------------ 内容构造
def _fmt_pct(v: float) -> str:
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def build_markdown(result: dict) -> str:
    """构造企业微信 markdown 消息（TopN）。"""
    r = result
    st = r.get("stats", {})
    bt = r.get("backtest_summary", {})
    tops = [x for x in r.get("results", []) if x.get("is_top")][: config.TOP_N]
    demo_tag = "  <font color=\"comment\">[演示数据]</font>" if r.get("is_demo") else ""
    lines = [
        f"# {config.DISPLAY_NAME}{demo_tag}",
        f"> 扫描时间：<font color=\"comment\">{r.get('scan_time', '')}</font>",
        f"> 交易日：{r.get('market_date', '')}　数据源：{r.get('source', '')}",
        f"> 全市场 **{st.get('universe_total', 0)}** 只 → 过滤后 **{st.get('after_filter', 0)}** 只 → 命中 **{st.get('signals', 0)}** 只",
        f"> 胜率口径：信号后持有 **{bt.get('hold_days', config.HOLD_DAYS)}** 个交易日，回看 **{bt.get('backtest_years', config.BACKTEST_YEARS)}** 年",
        "",
        f"**历史胜率 Top {len(tops)}**",
    ]
    if not tops:
        lines.append("> 今日无符合「连续跌破布林下轨后阳线收复」的标的。")
    for i, x in enumerate(tops, 1):
        wr = x.get("win_rate", 0) * 100
        color = "warning" if x.get("pct_chg", 0) >= 0 else "info"
        sample = "" if x.get("sample_ok") else " <font color=\"comment\">样本偏少</font>"
        lines.append(
            f"> **{i}. {x['code']} {x['name']}**　{x.get('price', 0):.2f} "
            f"<font color=\"{color}\">{_fmt_pct(x.get('pct_chg', 0))}</font>"
        )
        lines.append(
            f"> 胜率 <font color=\"warning\">**{wr:.1f}%**</font>　"
            f"均收益 {_fmt_pct(x.get('avg_return', 0))}　"
            f"信号 {x.get('signal_count', 0)} 次/{x.get('wins', 0)} 胜　"
            f"破轨 {x.get('break_days', 0)} 天{sample}"
        )
    lines += [
        "",
        f"[查看完整选股结果与布林带走势]({config.WEB_BASE_URL})",
        "<font color=\"comment\">提示：本结果由程序自动生成，仅供研究参考，不构成投资建议。</font>",
    ]
    return _with_keyword("\n".join(lines))


def build_text(result: dict) -> str:
    """纯文本版本，用于自建应用 / @提醒。"""
    r = result
    tops = [x for x in r.get("results", []) if x.get("is_top")][: config.TOP_N]
    head = [
        f"{config.DISPLAY_NAME}（{r.get('scan_time', '')}）",
        f"全市场 {r.get('stats', {}).get('universe_total', 0)} 只 → 命中 {r.get('stats', {}).get('signals', 0)} 只",
        f"历史胜率 Top {len(tops)}（持有 {config.HOLD_DAYS} 日）：",
    ]
    body = [
        f"{i}. {x['code']} {x['name']}  {x.get('price', 0):.2f} {_fmt_pct(x.get('pct_chg', 0))}  "
        f"胜率 {x.get('win_rate', 0) * 100:.1f}%  均收益 {_fmt_pct(x.get('avg_return', 0))}  信号{x.get('signal_count', 0)}次"
        for i, x in enumerate(tops, 1)
    ]
    return _with_keyword("\n".join(head + body + [f"详情：{config.WEB_BASE_URL}"]))


# ------------------------------------------------------------------ 通道
def _post(url: str, payload: dict, timeout: int = 10) -> tuple[bool, str]:
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        data = resp.json()
        code = data.get("errcode", 0)
        if code == 0:
            return True, "ok"
        hint = _ERR_HINT.get(code, "")
        detail = f"errcode={code} errmsg={data.get('errmsg')}"
        if hint:
            detail += f"｜建议：{hint}"
        return False, detail
    except Exception as exc:  # noqa: BLE001
        return False, f"请求异常：{str(exc)[:180]}"


def push_webhook(result: dict) -> dict:
    md = build_markdown(result)
    ok, msg = _post(config.WECOM_WEBHOOK, {"msgtype": "markdown", "markdown": {"content": md}})
    if ok:
        _write_preview(md, "企业微信推送内容（已成功发送到群）")
    extra = None
    mobiles = [m.strip() for m in config.WECOM_MENTION_MOBILES.split(",") if m.strip()]
    if ok and (mobiles or config.WECOM_MENTION_ALL):
        payload = {
            "msgtype": "text",
            "text": {
                "content": _with_keyword("选股结果已推送，请查看上方消息。"),
                "mentioned_list": mobiles,
                "mentioned_mobile_list": mobiles,
            },
        }
        if config.WECOM_MENTION_ALL:
            payload["text"]["mentioned_list"] = ["@all"]
        extra = _post(config.WECOM_WEBHOOK, payload)[1]
    return {"channel": "wecom_webhook", "ok": ok, "detail": msg, "extra": extra, "content": md}


def push_app(result: dict) -> dict:
    token_url = (
        f"https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        f"?corpid={config.WECOM_CORP_ID}&corpsecret={config.WECOM_CORP_SECRET}"
    )
    try:
        tk = requests.get(token_url, timeout=10).json()
        if tk.get("errcode", 0) != 0:
            return {"channel": "wecom_app", "ok": False, "detail": str(tk)}
        send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={tk['access_token']}"
        payload = {
            "touser": config.WECOM_TO_USER,
            "msgtype": "text",
            "agentid": int(config.WECOM_AGENT_ID),
            "text": {"content": build_text(result)},
            "safe": 0,
        }
        ok, msg = _post(send_url, payload)
        return {"channel": "wecom_app", "ok": ok, "detail": msg}
    except Exception as exc:  # noqa: BLE001
        return {"channel": "wecom_app", "ok": False, "detail": str(exc)[:200]}


def _write_preview(text: str, title: str) -> str:
    """把推送内容落盘，供网页「推送内容预览」面板展示。"""
    path = config.RESULT_DIR / "push_preview.md"
    try:
        path.write_text(
            f"# {title}\n\n生成时间：{now_cn():%Y-%m-%d %H:%M:%S}\n\n---\n\n{text}\n",
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("推送预览写入失败: %s", exc)
    return str(path)


def push_local(result: dict) -> dict:
    """未配置企业微信时，把同样的内容落盘，网页可预览。"""
    text = build_markdown(result)
    path = _write_preview(text, "企业微信推送预览（未配置 Webhook，本地预览）")
    return {
        "channel": "local_preview",
        "ok": True,
        "detail": "未配置企业微信，已生成推送预览文件",
        "preview_file": path,
        "content": text,
    }


def notify(result: dict) -> dict:
    """按优先级选择推送通道：群机器人 → 自建应用 → 本地预览。"""
    if config.WECOM_WEBHOOK:
        out = push_webhook(result)
        if out["ok"]:
            return out
        log.warning("企业微信机器人推送失败: %s，降级为本地预览", out["detail"])
        fallback = push_local(result)
        fallback["failed_channel"] = out
        return fallback
    if config.WECOM_CORP_ID and config.WECOM_CORP_SECRET and config.WECOM_AGENT_ID:
        out = push_app(result)
        if out["ok"]:
            return out
        log.warning("企业微信应用推送失败: %s，降级为本地预览", out["detail"])
    return push_local(result)


def test_push() -> dict:
    """连通性自检：发一条明确的测试消息，返回可直接照着排查的诊断信息。"""
    if not config.WECOM_WEBHOOK:
        return {
            "ok": False,
            "configured": False,
            "channel": "wecom_webhook",
            "detail": "尚未配置 WECOM_WEBHOOK。请到企业微信群 →「消息推送 / 群机器人」复制 Webhook 地址",
            "hint": "export WECOM_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'",
            "keyword": "未设置",
        }
    text = _with_keyword("【选股系统·连通性测试】收到本条消息，说明企业微信推送已打通。")
    ok, detail = _post(config.WECOM_WEBHOOK, {"msgtype": "text", "text": {"content": text}})
    return {
        "ok": ok,
        "configured": True,
        "channel": "wecom_webhook",
        "detail": "测试消息已发出，请到企业微信群查看" if ok else detail,
        "keyword": config.WECOM_KEYWORD or "未设置",
        "hint": "" if ok else "排查顺序：Webhook 是否完整复制 → 机器人是否被移除 → 安全设置里的关键词/IP 白名单 → 服务器出口 IP 是否在白名单",
    }
