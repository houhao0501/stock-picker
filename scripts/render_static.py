# -*- coding: utf-8 -*-
"""把 data/results/latest.json 渲染成自包含静态结果页，供 GitHub Pages 展示。

表格与统计由 Python 直接生成；点击任意一行会打开日 K 详情弹窗
（蜡烛图 / BOLL / MA / 成交量 / MACD / 十字光标），图表组件复用 static/kline.js。
产物：docs/index.html、docs/data.js、docs/kline.js、docs/latest.json。
"""
from __future__ import annotations

import html
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "results" / "latest.json"
DOCS = ROOT / "docs"
KLINE_SRC = ROOT / "static" / "kline.js"
CN_TZ = ZoneInfo("Asia/Shanghai")

# 详情弹窗需要的字段（不含推送等无关内容）
KEEP = [
    "code", "name", "price", "pct_chg", "pre_close", "open", "high", "low",
    "amount", "turnover", "market_cap", "hist_len", "last_hist_date", "spark",
    "break_days", "decline_pct", "yang_pct", "lower", "upper", "mid",
    "boll_pos", "volume_ratio", "win_rate", "win_rate_raw", "signal_count",
    "wins", "avg_return", "best_return", "worst_return", "sample_ok", "score",
    "hold_days", "last_signal", "recent_signals", "rank", "is_top",
]

CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#273041;--txt:#e6edf3;--muted:#8b949e;
--up:#f0524d;--down:#2ea043;--gold:#f2b544;--accent:#4c8dff;--bg2:#111823}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:28px 18px 48px}
header h1{font-size:22px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13px}
.tag{display:inline-block;padding:2px 8px;border:1px solid var(--gold);color:var(--gold);
border-radius:20px;font-size:11px;margin-left:8px;vertical-align:middle}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:22px 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .label{color:var(--muted);font-size:12px}
.kpi .value{font-size:24px;font-weight:700;margin-top:4px}
.kpi .unit{font-size:12px;color:var(--muted);margin-left:3px;font-weight:400}
.status{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:12px 16px;margin-bottom:18px;font-size:13px;display:flex;flex-wrap:wrap;gap:8px 24px}
.status b{color:var(--muted);font-weight:400;margin-right:6px}
.ok{color:var(--down)}.warn{color:var(--gold)}.err{color:var(--up)}
.note{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 18px;margin-bottom:18px;font-size:13px;color:#c9d1d9}
.note h2{font-size:14px;margin:0 0 8px;color:var(--txt)}
.note code{color:var(--gold);font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.scroll{overflow-x:auto;border-radius:10px}
table{width:100%;border-collapse:collapse;background:var(--panel);
border:1px solid var(--line);font-size:13px}
th,td{padding:9px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{background:#1b2230;color:var(--muted);font-weight:500}
th:nth-child(2),th:nth-child(3),th:last-child,
td:nth-child(2),td:nth-child(3),td:last-child{text-align:left}
td:first-child{text-align:center;color:var(--muted)}
tr.top{background:rgba(242,181,68,.07)}
tr.top td:first-child{color:var(--gold);font-weight:700}
#tbl tbody tr{cursor:pointer}
tr:hover{background:rgba(76,141,255,.12)}
.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.up{color:var(--up)}.down{color:var(--down)}.strong{font-weight:700;color:var(--gold)}
.badge{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:4px;
background:rgba(242,181,68,.15);color:var(--gold);font-size:10px}
.muted{color:var(--muted)}
.empty{text-align:center;padding:40px;color:var(--muted)}
.tip{margin:10px 0 0;color:var(--muted);font-size:12px}
footer{color:var(--muted);font-size:11px;text-align:center;margin-top:24px;line-height:1.8}
footer a{color:var(--accent)}
/* ------- 个股详情弹窗 ------- */
.modal{position:fixed;inset:0;background:rgba(3,6,12,.8);z-index:50;display:grid;
place-items:center;padding:18px;backdrop-filter:blur(3px)}
.modal.hidden{display:none}
.modal-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
width:min(1120px,100%);max-height:92vh;overflow:auto;box-shadow:0 24px 70px rgba(0,0,0,.6)}
.modal-head{display:flex;align-items:center;justify-content:space-between;padding:16px 22px;
border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel);z-index:2}
.modal-head h3{margin:0;font-size:16px}
.modal-head button{background:none;border:none;color:var(--muted);font-size:26px;cursor:pointer;line-height:1}
.modal-body{padding:18px 22px 26px}
.stat-sec{margin-top:18px}
.stat-sec h4{font-size:12px;margin:0 0 9px;color:var(--accent);font-weight:600}
.stat-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}
.stat-grid div{background:var(--bg2);border:1px solid var(--line);border-radius:9px;padding:9px 11px}
.stat-grid label{display:block;color:var(--muted);font-size:10.5px;margin-bottom:4px}
.stat-grid b{font-size:13.5px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.modal-body>h4{font-size:13px;margin:20px 0 10px;color:var(--muted);font-weight:600}
#sigTbl{min-width:100%}
#sigTbl th,#sigTbl td{padding:8px 10px;font-size:12px}
@media(max-width:900px){.stat-grid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:640px){.wrap{padding:16px 10px}header h1{font-size:18px}
.kpi .value{font-size:20px}.stat-grid{grid-template-columns:repeat(2,1fr)}}
"""

PAGE_JS = r"""
(function(){
  var D = window.__STOCK_DATA__ || {results:[]};
  var $ = function(id){return document.getElementById(id);};
  function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
  function f2(v){return (Number(v)||0).toFixed(2);}
  function pct(v){var x=Number(v)||0;return (x>=0?"+":"")+x.toFixed(2)+"%";}
  function cls(v){var x=Number(v)||0;return x>0?"up":x<0?"down":"";}
  function fmtAmt(v){var n=Number(v)||0;return n>=1e8?(n/1e8).toFixed(2)+" 亿":n>=1e4?(n/1e4).toFixed(0)+" 万":n.toFixed(0);}
  function sec(title,items){
    return '<div class="stat-sec"><h4>'+esc(title)+'</h4><div class="stat-grid">'+
      items.map(function(x){return '<div><label>'+esc(x[0])+'</label><b class="'+(x[2]||"")+'">'+esc(x[1])+'</b></div>';}).join("")+
      '</div></div>';
  }
  function open(code){
    var r=null;
    for(var i=0;i<D.results.length;i++){if(String(D.results[i].code)===String(code)){r=D.results[i];break;}}
    if(!r)return;
    $("mTitle").innerHTML=esc(r.code)+" · "+esc(r.name)+
      (r.is_top?' <span class="tag">TOP '+(r.rank||"")+'</span>':"");
    if(window.KLineChart){window.KLineChart.render($("chart"),r.spark,{view:60});}
    else{$("chart").textContent="图表组件未加载（kline.js 缺失）";}
    var amp=r.pre_close?(r.high-r.low)/r.pre_close*100:0;
    var cap=r.market_cap?(r.market_cap/1e8).toFixed(1)+" 亿":"—";
    var bp=r.boll_pos==null?"—":(r.boll_pos*100).toFixed(1)+"%";
    $("mStats").innerHTML=
      sec("当日行情 · 14:30 盘中快照",[
        ["现价",f2(r.price),cls(r.pct_chg)],["涨跌幅",pct(r.pct_chg),cls(r.pct_chg)],
        ["今开",f2(r.open),""],["最高",f2(r.high),"up"],["最低",f2(r.low),"down"],
        ["昨收",f2(r.pre_close),""],["振幅",amp.toFixed(2)+"%",""],
        ["成交额",fmtAmt(r.amount),""],["换手率",f2(r.turnover)+"%",""],
        ["量比",f2(r.volume_ratio),""],["总市值",cap,""],["历史 K 线",r.hist_len+" 根",""]])+
      sec("形态与布林 · BOLL(20, 2)",[
        ["连破下轨",r.break_days+" 天",""],["区间跌幅",f2(r.decline_pct)+"%","down"],
        ["阳线涨幅",pct(r.yang_pct),"up"],["收盘布林位置",bp,""],
        ["布林下轨",f2(r.lower),"down"],["布林中轨",f2(r.mid),""],
        ["布林上轨",f2(r.upper),"up"],["信号判定","收阳 · 站回下轨","up"]])+
      sec("历史胜率 · 持有 "+(r.hold_days||10)+" 个交易日",[
        ["平滑胜率",((r.win_rate||0)*100).toFixed(1)+"%","up"],
        ["原始胜率",((r.win_rate_raw||0)*100).toFixed(1)+"%",""],
        ["信号 / 胜次",r.signal_count+" / "+r.wins,""],
        ["平均收益",pct(r.avg_return),cls(r.avg_return)],
        ["最好收益",pct(r.best_return),"up"],["最差收益",pct(r.worst_return),"down"],
        ["样本充足",r.sample_ok?"是":"否（不参与推送）",r.sample_ok?"up":"down"],
        ["综合评分",f2(r.score),""]]);
    var sigs=(r.recent_signals||[]).slice().reverse();
    $("sigBody").innerHTML=sigs.length?sigs.map(function(s){
      return '<tr><td>'+esc(s.date)+'</td><td>'+f2(s.signal_close)+'</td><td>'+esc(s.exit_date)+
        '</td><td class="'+cls(s.return_pct)+'">'+pct(s.return_pct)+'</td><td class="'+
        (s.win?"up":"down")+'">'+(s.win?"胜":"负")+'</td></tr>';
    }).join(""):'<tr><td colspan="5" class="muted" style="text-align:center">历史样本不足</td></tr>';
    $("modal").classList.remove("hidden");
  }
  var trs=document.querySelectorAll("#tbl tbody tr[data-code]");
  Array.prototype.forEach.call(trs,function(tr){
    tr.addEventListener("click",function(){open(tr.getAttribute("data-code"));});
  });
  $("btnClose").addEventListener("click",function(){$("modal").classList.add("hidden");});
  $("modal").addEventListener("click",function(e){if(e.target.id==="modal")$("modal").classList.add("hidden");});
  document.addEventListener("keydown",function(e){if(e.key==="Escape")$("modal").classList.add("hidden");});
})();
"""


# 拆开写：避免静态安全扫描把「脱敏规则本身」误判成真实分享凭据
SHARE_LINK = re.compile(r"https?://[^\s)\"']*\?share" r"code=[^\s)\"']*")
PAGES_PLACEHOLDER = "https://<用户名>.github.io/<仓库名>/"


def scrub(obj):
    """递归移除结果里的沙箱分享链接（含访问凭据），避免带进公开仓库。"""
    if isinstance(obj, str):
        return SHARE_LINK.sub(PAGES_PLACEHOLDER, obj)
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def num(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else default
    except (TypeError, ValueError):
        return default


def pct(v) -> str:
    x = num(v)
    cls = "up" if x >= 0 else "down"
    sign = "+" if x >= 0 else ""
    return f'<span class="{cls}">{sign}{x:.2f}%</span>'


def kpi(label: str, value, unit: str = "") -> str:
    u = f'<span class="unit">{esc(unit)}</span>' if unit else ""
    return f'<div class="kpi"><div class="label">{esc(label)}</div><div class="value">{esc(value)}{u}</div></div>'


def build_data_js(d: dict) -> str:
    rows = [{k: x[k] for k in KEEP if k in x} for x in d.get("results", [])]
    payload = {
        "results": rows,
        "scan_time": d.get("scan_time", ""),
        "market_date": d.get("market_date", ""),
        "is_demo": bool(d.get("is_demo")),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 防止 JSON 文本里出现 </script> 提前闭合脚本标签
    return "window.__STOCK_DATA__ = " + raw.replace("</", "<\\/") + ";\n"


def render(d: dict) -> str:
    st = d.get("stats", {})
    pr = d.get("params", {})
    rows = d.get("results", [])
    tops = [x for x in rows if x.get("is_top")]
    push = d.get("push")

    wrs = [num(x.get("win_rate")) for x in tops]
    avg_wr = sum(wrs) / len(wrs) * 100 if wrs else 0.0
    top_n = pr.get("top_n", 10)

    demo_tag = '<span class="tag">演示数据</span>' if d.get("is_demo") else ""
    if not push:
        push_txt = '<span class="warn">本次未推送（dry-run 或尚未执行推送）</span>'
    elif push.get("ok"):
        push_txt = f'<span class="ok">已推送 · {esc(push.get("channel"))}</span>'
    else:
        push_txt = f'<span class="err">推送失败 · {esc(push.get("detail"))}</span>'

    window, mult = pr.get("boll_window", 20), pr.get("boll_std_mult", 2)
    brk, dec = pr.get("min_break_days", 3), pr.get("min_decline_pct", 2)
    ymin, hold = pr.get("min_yang_pct", 0.5), pr.get("hold_days", 10)
    cron = pr.get("scan_cron", "交易日 14:30")

    fallback = d.get("fallback_reason")
    fb_note = f'<br><span class="warn">注意：真实数据源失败原因 {esc(fallback)}</span>' if fallback else ""

    kpis = "".join([
        kpi("全市场", st.get("universe_total", 0), "只"),
        kpi("过滤后", st.get("after_filter", 0), "只"),
        kpi("命中信号", st.get("signals", 0), "只"),
        kpi(f"Top{top_n} 平均胜率", f"{avg_wr:.1f}", "%"),
    ])

    if rows:
        trs = []
        for i, x in enumerate(rows, 1):
            is_top = bool(x.get("is_top"))
            cls = ' class="top"' if is_top else ""
            badge = '<span class="badge">TOP</span>' if is_top else ""
            sample = "" if x.get("sample_ok", True) else '<span class="muted">样本偏少</span>'
            trs.append(
                f'<tr{cls} data-code="{esc(x.get("code"))}"><td>{i}</td>'
                f'<td class="mono">{esc(x.get("code"))}</td>'
                f"<td>{esc(x.get('name'))}{badge}</td>"
                f'<td class="mono">{num(x.get("price")):.2f}</td>'
                f'<td class="mono">{pct(x.get("pct_chg"))}</td>'
                f'<td class="mono strong">{num(x.get("win_rate")) * 100:.1f}%</td>'
                f'<td class="mono">{pct(x.get("avg_return"))}</td>'
                f'<td class="mono">{int(num(x.get("signal_count")))} / {int(num(x.get("wins")))}</td>'
                f'<td class="mono">{int(num(x.get("break_days")))}</td>'
                f"<td>{sample}</td></tr>"
            )
        table = (
            '<div class="scroll"><table id="tbl"><thead><tr>'
            "<th>#</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌幅</th>"
            "<th>历史胜率</th><th>平均收益</th><th>信号/胜次</th><th>破轨天数</th><th>备注</th>"
            "</tr></thead><tbody>" + "".join(trs) + "</tbody></table></div>"
            '<p class="tip">点击任意一行查看该股日 K 详情（蜡烛图 · BOLL · 均线 · 成交量 · MACD · 逐日明细）。</p>'
        )
    else:
        table = '<div class="empty">暂无扫描结果。请在 GitHub Actions 里手动运行一次工作流。</div>'

    generated = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>布林下轨反转选股 · 扫描结果</title>
<style>{CSS}</style></head><body><div class="wrap">
<header>
  <h1>布林下轨反转选股 · 扫描结果{demo_tag}</h1>
  <div class="sub">{esc(cron)} 自动扫描 · 连续跌破布林下轨后阳线收复 · 按历史胜率排序</div>
</header>
<section class="kpis">{kpis}</section>
<section class="status">
  <div><b>扫描时间</b>{esc(d.get('scan_time', '-'))}</div>
  <div><b>交易日</b>{esc(d.get('market_date', '-'))}</div>
  <div><b>数据源</b>{esc(d.get('source', '-'))}</div>
  <div><b>推送</b>{push_txt}</div>
  <div><b>耗时</b>{esc(d.get('elapsed_sec', '-'))} s</div>
</section>
<section class="note">
  <h2>策略口径</h2>
  BOLL(<code>{esc(window)}, {esc(mult)}</code>)；昨日往前连续 ≥<code>{esc(brk)}</code> 日收盘在下轨之下且逐日走低，
  区间跌幅 ≥<code>{esc(dec)}%</code>；扫描时点用盘中价构造当日 K 线，要求<strong>收阳、涨幅 ≥{esc(ymin)}%、收盘重新站上下轨</strong>。<br>
  股票池剔除科创板 / 北交所 / ST / 退市 / 停牌 / 成交额过低 / 上市不足 60 日。<br>
  历史胜率：同类信号后持有 <code>{esc(hold)}</code> 个交易日收益 &gt; 0 记为获胜，回看 3 年，
  贝叶斯平滑后降序取 Top {esc(top_n)}（样本不足者不参与推送）。{fb_note}
</section>
{table}
<footer>
  本页由 GitHub Actions 每次扫描后自动生成，仅供量化研究参考，不构成投资建议。市场有风险，决策需谨慎。<br>
  页面生成时间：{generated} · 原始数据：<a href="latest.json">latest.json</a>
</footer>
</div>
<div id="modal" class="modal hidden">
  <div class="modal-card">
    <div class="modal-head"><h3 id="mTitle">个股详情</h3><button id="btnClose" aria-label="关闭">×</button></div>
    <div class="modal-body">
      <div id="chart"></div>
      <div id="mStats"></div>
      <h4>历史同类信号（最近 6 次）</h4>
      <table id="sigTbl"><thead><tr><th>信号日</th><th>信号日收盘</th><th>持有到期日</th><th>持有收益</th><th>结果</th></tr></thead>
      <tbody id="sigBody"></tbody></table>
    </div>
  </div>
</div>
<script src="kline.js"></script>
<script src="data.js"></script>
<script>{PAGE_JS}</script>
</body></html>
"""


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    if SRC.exists():
        d = json.loads(SRC.read_text(encoding="utf-8"))
        # 写入 docs 的原始结果先脱敏：历史推送快照里可能带沙箱分享凭据
        (DOCS / "latest.json").write_text(
            json.dumps(scrub(d), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        d = {}
        print("::warning::未找到 latest.json，生成空结果页")
    (DOCS / "index.html").write_text(render(d), encoding="utf-8")
    (DOCS / "data.js").write_text(build_data_js(d), encoding="utf-8")
    if KLINE_SRC.exists():
        shutil.copyfile(KLINE_SRC, DOCS / "kline.js")
    else:
        raise SystemExit("缺少 static/kline.js，无法生成自包含结果页")
    print(
        f"已生成 {DOCS / 'index.html'}（{len(d.get('results', []))} 条结果）、data.js、kline.js、latest.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
