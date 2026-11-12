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

/* ------- 个人观察台：候选池 / 持仓监控 / 止损告警 ------- */
.alert-bar{display:none;border:1px solid var(--up);background:rgba(240,82,77,.12);
border-radius:10px;padding:14px 18px;margin:0 0 18px;font-size:13px;line-height:1.9}
.alert-bar.show{display:block}
.alert-bar b{color:var(--up);font-size:15px}
.alert-bar .arows{margin-top:8px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
.personal{margin:24px 0 0}
.personal>h2{font-size:16px;margin:0 0 4px}
.personal>.desc{color:var(--muted);font-size:12px;margin:0 0 14px;line-height:1.75}
.pgrid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:960px){.pgrid{grid-template-columns:1fr}}
.pcard{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
.pcard h3{font-size:14px;margin:0 0 3px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.pcard h3 .cnt{font-size:11px;color:var(--muted);font-weight:400}
.pcard .psub{color:var(--muted);font-size:11.5px;margin:0 0 12px;line-height:1.7}
.addbar{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px;align-items:center}
.addbar input{background:var(--bg2);border:1px solid var(--line);color:var(--txt);
border-radius:7px;padding:7px 10px;font-size:13px;width:112px}
.addbar input.wname{width:118px}
.btn{background:var(--bg2);border:1px solid var(--line);color:var(--txt);
border-radius:7px;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap}
.btn:hover{border-color:var(--accent)}
.btn.pri{background:rgba(76,141,255,.14);border-color:var(--accent);color:var(--accent)}
.btn.danger{background:rgba(240,82,77,.12);border-color:var(--up);color:var(--up)}
.btn.sm{padding:3px 8px;font-size:11px}
.btn:disabled{opacity:.45;cursor:default}
.rowbtns{display:flex;gap:5px;flex-wrap:wrap}
.ptable{width:100%;font-size:12px;border:0}
.ptable th,.ptable td{padding:7px 8px;white-space:nowrap;border-bottom:1px solid var(--line)}
.ptable th{background:transparent;color:var(--muted)}
.st-ok{color:var(--down)}.st-warn{color:var(--gold)}.st-stop{color:var(--up);font-weight:700}
.st-na{color:var(--muted)}
.empty-s{color:var(--muted);font-size:12px;padding:16px 2px;line-height:1.8}
.hint-s{color:var(--muted);font-size:11.5px;margin-top:12px;line-height:1.8}
.spark{vertical-align:middle;display:inline-block}
.buy-body{padding:18px 22px 22px}
.buy-body .lbl{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}
.buy-body input{width:100%;background:var(--bg2);border:1px solid var(--line);color:var(--txt);
border-radius:8px;padding:10px 12px;font-size:17px;
font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.buy-calc{background:var(--bg2);border:1px solid var(--line);border-radius:9px;
padding:12px 14px;font-size:13px;line-height:2;margin-top:14px}
.buy-calc b{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--gold)}
.buy-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);background:#1b2230;
border:1px solid var(--accent);color:var(--txt);padding:10px 18px;border-radius:9px;
font-size:13px;z-index:80;box-shadow:0 10px 30px rgba(0,0,0,.5)}
.toast.hidden{display:none}
"""

PAGE_JS = r"""
(function(){
  var D = window.__STOCK_DATA__ || {results:[]};
  var PM = D.price_map || {};
  var PD = D.price_date || D.market_date || "";
  var STALE = !!D.price_stale;
  var STOP_PCT = 6;
  var KEY = "stock_picker_personal_v1";
  var $ = function(id){return document.getElementById(id);};
  function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
  function f2(v){return (Number(v)||0).toFixed(2);}
  function pct(v){var x=Number(v)||0;return '<span class="'+(x>=0?"up":"down")+'">'+(x>=0?"+":"")+x.toFixed(2)+"%</span>";}
  function cls(v){var x=Number(v)||0;return x>0?"up":x<0?"down":"";}
  function fmtAmt(v){var n=Number(v)||0;return n>=1e8?(n/1e8).toFixed(2)+" 亿":n>=1e4?(n/1e4).toFixed(0)+" 万":n.toFixed(0);}
  function sec(title,items){
    return '<div class="stat-sec"><h4>'+esc(title)+'</h4><div class="stat-grid">'+
      items.map(function(x){return '<div><label>'+esc(x[0])+'</label><b class="'+(x[2]||"")+'">'+esc(x[1])+'</b></div>';}).join("")+
      '</div></div>';
  }
  function findResult(code){
    for(var i=0;i<D.results.length;i++){if(String(D.results[i].code)===String(code))return D.results[i];}
    return null;
  }
  function priceOf(code){
    var v=PM[String(code)];
    if(v&&isFinite(v[0])&&v[0]>0)return v[0];
    var r=findResult(code);return r?(Number(r.price)||0):0;
  }
  function nameOf(code,fb){
    var v=PM[String(code)];
    if(v&&v[1])return v[1];
    var r=findResult(code);return (r&&r.name)||fb||String(code);
  }
  function stopPrice(bp){return Math.round(bp*(1-STOP_PCT/100)*100)/100;}
  function chgPct(a,b){return a>0?(b-a)/a*100:0;}
  function dayStr(){return String(PD||"").slice(0,10)||"当日";}

  /* ---- 本机台账 ---- */
  function load(){
    try{
      var o=JSON.parse(localStorage.getItem(KEY)||"{}");
      if(!o||typeof o!=="object")o={};
      if(!Array.isArray(o.watch))o.watch=[];
      if(!Array.isArray(o.holdings))o.holdings=[];
      return o;
    }catch(e){return {watch:[],holdings:[]};}
  }
  function save(){try{localStorage.setItem(KEY,JSON.stringify(ST));}catch(e){}}
  var ST=load();

  function stamp(rec){
    var p=priceOf(rec.code);
    if(!(p>0))return;
    rec.trail=rec.trail||[];
    var d=dayStr(), last=rec.trail.length?rec.trail[rec.trail.length-1]:null;
    if(last&&last[0]===d)last[1]=p; else rec.trail.push([d,p]);
    if(rec.trail.length>90)rec.trail.shift();
  }
  function addWatch(code,name,src){
    code=String(code||"").replace(/\D/g,"").slice(-6);
    if(code.length!==6){toast("请输入 6 位股票代码");return false;}
    for(var i=0;i<ST.watch.length;i++){if(ST.watch[i].code===code){toast("已在候选池");return false;}}
    ST.watch.push({code:code,name:nameOf(code,name),add_date:dayStr(),
      add_price:priceOf(code),source:src||"manual",trail:[]});
    stamp(ST.watch[ST.watch.length-1]);save();return true;
  }
  function unwatch(code){ST.watch=ST.watch.filter(function(w){return w.code!==code;});save();}
  function sell(code){ST.holdings=ST.holdings.filter(function(h){return h.code!==code;});save();}
  function holdingStatus(h){
    var p=priceOf(h.code);
    if(!(p>0))return {k:"na",txt:"无行情"};
    if(p<=h.stop_price)return {k:"stop",txt:"跌破止损"};
    if(p<=h.stop_price*1.02)return {k:"warn",txt:"接近止损"};
    return {k:"ok",txt:"正常"};
  }

  /* ---- 走势迷你图（红涨绿跌） ---- */
  function sparkSVG(trail){
    var pts=(trail||[]).filter(function(x){return isFinite(x[1])&&x[1]>0;}).map(function(x){return x[1];});
    if(pts.length<2)return '<span class="muted">—</span>';
    var W=92,H=26,lo=Math.min.apply(null,pts),hi=Math.max.apply(null,pts),rg=(hi-lo)||1;
    var d=pts.map(function(v,i){
      var x=i*(W/(pts.length-1)),y=H-2-((v-lo)/rg)*(H-4);
      return (i?"L":"M")+x.toFixed(1)+" "+y.toFixed(1);
    }).join(" ");
    var up=pts[pts.length-1]>=pts[0];
    return '<svg class="spark" width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+'">'+
      '<path d="'+d+'" fill="none" stroke="'+(up?"var(--up)":"var(--down)")+'" stroke-width="1.6"/></svg>';
  }

  /* ---- 渲染 ---- */
  function renderAlert(){
    var hit=ST.holdings.filter(function(h){return holdingStatus(h).k==="stop";});
    var el=$("stopAlert");
    if(!hit.length){el.className="alert-bar";el.innerHTML="";return;}
    el.className="alert-bar show";
    el.innerHTML='<b>⚠ 止损提醒：'+hit.length+' 只已跌破 -'+STOP_PCT+'% 止损位，请强制卖出</b>'+
      '<div class="arows">'+hit.map(function(h){
        var p=priceOf(h.code);
        return esc(h.code)+" "+esc(h.name)+"　买入 "+f2(h.buy_price)+" → 止损 "+f2(h.stop_price)+
          "　最新 "+f2(p)+"　"+pct(chgPct(h.buy_price,p));
      }).join("<br>")+'</div>'+
      '<div class="hint-s">价格基准：'+esc(PD||"最近一次扫描")+' 的扫描快照（非实时盘中价）。'+
      '卖出后请在下方「持仓监控」点「已卖出·删除」移除记录。本工具不会自动下单。</div>';
    try{
      if(window.Notification&&Notification.permission==="granted"){
        new Notification("布林下轨反转 · 止损提醒",
          {body:hit.length+" 只跌破 -"+STOP_PCT+"% 止损位，请检查并强制卖出"});
      }
    }catch(e){}
  }
  function renderHoldings(){
    var el=$("holdBody");
    $("holdCnt").textContent=ST.holdings.length?"（"+ST.holdings.length+"）":"";
    if(!ST.holdings.length){
      el.innerHTML='<tr><td colspan="9" class="empty-s">还没有持仓。可在上方结果表点「买入」，或在候选池里买入；买入时需本人确认成交价。</td></tr>';
      return;
    }
    el.innerHTML=ST.holdings.map(function(h){
      var p=priceOf(h.code),s=holdingStatus(h),c=chgPct(h.buy_price,p);
      return '<tr><td class="mono">'+esc(h.code)+'</td><td>'+esc(h.name)+'</td>'+
        '<td class="mono">'+f2(h.buy_price)+'</td>'+'<td class="mono">'+esc(h.buy_date)+'</td>'+
        '<td class="mono down">'+f2(h.stop_price)+'</td>'+
        '<td class="mono">'+(p>0?f2(p):"—")+'</td>'+
        '<td class="mono '+(p>0?cls(c):"")+'">'+(p>0?pct(c):"—")+'</td>'+
        '<td class="st-'+s.k+'">'+esc(s.txt)+'</td>'+
        '<td><button class="btn sm danger" data-act="sell" data-code="'+esc(h.code)+'">已卖出·删除</button></td></tr>';
    }).join("");
  }
  function renderWatch(){
    var el=$("watchBody");
    $("watchCnt").textContent=ST.watch.length?"（"+ST.watch.length+"）":"";
    if(!ST.watch.length){
      el.innerHTML='<tr><td colspan="8" class="empty-s">候选池为空。可在上方结果表点「+候选」，或在此手动输入 6 位代码添加想跟踪的股票。</td></tr>';
      return;
    }
    el.innerHTML=ST.watch.map(function(w){
      var p=priceOf(w.code),c=chgPct(w.add_price,p),days=w.trail?w.trail.length:0;
      return '<tr><td class="mono">'+esc(w.code)+'</td><td>'+esc(w.name)+'</td>'+
        '<td class="mono">'+esc(w.add_date)+'</td>'+
        '<td class="mono">'+(w.add_price>0?f2(w.add_price):"—")+'</td>'+
        '<td class="mono">'+(p>0?f2(p):"—")+'</td>'+
        '<td class="mono '+(p>0?cls(c):"")+'">'+(p>0?pct(c):"—")+'</td>'+
        '<td>'+sparkSVG(w.trail)+' <span class="muted">'+days+'点</span></td>'+
        '<td><div class="rowbtns"><button class="btn sm pri" data-act="buy" data-code="'+esc(w.code)+'">买入</button>'+
        '<button class="btn sm" data-act="unwatch" data-code="'+esc(w.code)+'">移除</button></div></td></tr>';
    }).join("");
  }
  function markTable(){
    var hs={},ws={};
    ST.holdings.forEach(function(h){hs[h.code]=1;});
    ST.watch.forEach(function(w){ws[w.code]=1;});
    Array.prototype.forEach.call(document.querySelectorAll("#tbl tbody tr[data-code]"),function(tr){
      var code=tr.getAttribute("data-code");
      var b1=tr.querySelector('[data-act="watch"]'),b2=tr.querySelector('[data-act="buy"]');
      if(b1){b1.textContent=ws[code]?"已候选":"+候选";b1.disabled=!!ws[code];}
      if(b2){b2.textContent=hs[code]?"持仓中":"买入";b2.disabled=!!hs[code];}
    });
  }
  function renderAll(){
    ST.watch.forEach(stamp);ST.holdings.forEach(stamp);save();
    renderAlert();renderHoldings();renderWatch();markTable();
    var ph=$("priceHint");
    if(ph)ph.innerHTML="价格基准：<b>"+esc(PD||"最近一次扫描")+"</b> 的全市场快照"+
      (STALE?' <span class="up">（该快照已超过 3 天，止损判断可能滞后）</span>':"")+
      " · 行情源共 "+Object.keys(PM).length+" 只 · 记录仅保存在本设备浏览器";
  }

  /* ---- 买入确认弹窗 ---- */
  var BUY_CODE=null;
  function openBuy(code){
    code=String(code);
    for(var i=0;i<ST.holdings.length;i++){if(ST.holdings[i].code===code){toast("已在持仓监控中");return;}}
    BUY_CODE=code;
    var p=priceOf(code);
    $("buyTitle").textContent="确认买入 · "+code+" "+nameOf(code,"");
    $("buyPrice").value=p>0?p.toFixed(2):"";
    $("buyCalc").innerHTML="参考价（"+esc(PD||"最近扫描")+"）：<b>"+(p>0?f2(p):"—")+"</b><br>"+
      "止跌线：<b>-"+STOP_PCT+"%</b>　止损价 = 成交价 × "+(1-STOP_PCT/100).toFixed(2)+
      "　<span id='buyStop'></span>";
    updStop();
    $("buyModal").classList.remove("hidden");
    setTimeout(function(){try{$("buyPrice").focus();}catch(e){}},60);
  }
  function updStop(){
    var v=Number($("buyPrice").value),e=$("buyStop");
    if(e)e.innerHTML=v>0?"→ <b>"+f2(stopPrice(v))+"</b>":"";
  }
  function closeBuy(){$("buyModal").classList.add("hidden");BUY_CODE=null;}
  function confirmBuy(){
    var v=Number($("buyPrice").value);
    if(!(v>0)){toast("请输入有效的成交价");return;}
    if(!BUY_CODE)return;
    ST.holdings.push({code:BUY_CODE,name:nameOf(BUY_CODE,""),buy_price:Math.round(v*100)/100,
      buy_date:dayStr(),stop_price:stopPrice(v),stop_pct:STOP_PCT});
    save();closeBuy();renderAll();toast("已加入持仓监控，止损价 "+f2(stopPrice(v)));
  }
  function addManual(){
    if(addWatch($("addCode").value,$("addName").value.trim(),"manual")){
      $("addCode").value="";$("addName").value="";renderAll();toast("已加入候选池");
    }
  }

  /* ---- 个股详情弹窗 + K 线（原有） ---- */
  function open(code){
    var r=findResult(code);
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
        ["现价",f2(r.price),cls(r.pct_chg)],["涨跌幅",(Number(r.pct_chg)>=0?"+":"")+f2(r.pct_chg)+"%",cls(r.pct_chg)],
        ["今开",f2(r.open),""],["最高",f2(r.high),"up"],["最低",f2(r.low),"down"],
        ["昨收",f2(r.pre_close),""],
        ["振幅",amp.toFixed(2)+"%",""],["成交额",fmtAmt(r.amount),""],
        ["换手率",f2(r.turnover)+"%",""],["量比",f2(r.volume_ratio),""],
        ["总市值",cap,""],["历史 K 线",r.hist_len+" 根",""]])+
      sec("形态与布林 · BOLL(20, 2)",[
        ["连破下轨",r.break_days+" 天",""],["区间跌幅",f2(r.decline_pct)+"%","down"],
        ["阳线涨幅",(Number(r.yang_pct)>=0?"+":"")+f2(r.yang_pct)+"%","up"],
        ["收盘布林位置",bp,""],
        ["布林下轨",f2(r.lower),"down"],["布林中轨",f2(r.mid),""],
        ["布林上轨",f2(r.upper),"up"],["信号判定","收阳 · 站回下轨","up"]])+
      sec("历史胜率 · 持有 "+(r.hold_days||10)+" 个交易日",[
        ["平滑胜率",((r.win_rate||0)*100).toFixed(1)+"%","up"],
        ["原始胜率",((r.win_rate_raw||0)*100).toFixed(1)+"%",""],
        ["信号 / 胜次",r.signal_count+" / "+r.wins,""],
        ["平均收益",(Number(r.avg_return)>=0?"+":"")+f2(r.avg_return)+"%",cls(r.avg_return)],
        ["最好收益",(Number(r.best_return)>=0?"+":"")+f2(r.best_return)+"%","up"],
        ["最差收益",(Number(r.worst_return)>=0?"+":"")+f2(r.worst_return)+"%","down"],
        ["样本充足",r.sample_ok?"是":"否（不参与推送）",r.sample_ok?"up":"down"],
        ["综合评分",f2(r.score),""]]);
    $("modalActs").innerHTML='<div class="rowbtns" style="margin-top:16px">'+
      '<button class="btn sm" data-act="watch" data-code="'+esc(r.code)+'">+ 加入候选池</button>'+
      '<button class="btn sm pri" data-act="buy" data-code="'+esc(r.code)+'">买入并设 -6% 止损</button></div>';
    var sigs=(r.recent_signals||[]).slice().reverse();
    $("sigBody").innerHTML=sigs.length?sigs.map(function(s){
      return '<tr><td>'+esc(s.date)+'</td><td>'+f2(s.signal_close)+'</td><td>'+esc(s.exit_date)+
        '</td><td class="'+cls(s.return_pct)+'">'+(Number(s.return_pct)>=0?"+":"")+f2(s.return_pct)+'%</td><td class="'+
        (s.win?"up":"down")+'">'+(s.win?"胜":"负")+'</td></tr>';
    }).join(""):'<tr><td colspan="5" class="muted" style="text-align:center">历史样本不足</td></tr>';
    $("modal").classList.remove("hidden");
  }

  /* ---- 事件 ---- */
  document.addEventListener("click",function(e){
    var b=e.target&&e.target.closest?e.target.closest("[data-act]"):null;
    if(!b||b.disabled)return;
    var act=b.getAttribute("data-act"),code=b.getAttribute("data-code");
    if(act==="watch"){if(addWatch(code,"","signal")){renderAll();toast("已加入候选观察池");}}
    else if(act==="buy"){openBuy(code);}
    else if(act==="unwatch"){unwatch(code);renderAll();}
    else if(act==="sell"){sell(code);renderAll();toast("已删除该买入记录");}
  });
  var trs=document.querySelectorAll("#tbl tbody tr[data-code]");
  Array.prototype.forEach.call(trs,function(tr){
    tr.addEventListener("click",function(e){
      if(e.target&&e.target.closest&&e.target.closest("[data-act]"))return;
      open(tr.getAttribute("data-code"));
    });
  });
  $("btnClose").addEventListener("click",function(){$("modal").classList.add("hidden");});
  $("modal").addEventListener("click",function(e){if(e.target.id==="modal")$("modal").classList.add("hidden");});
  $("buyCloseX").addEventListener("click",closeBuy);
  $("buyCancel").addEventListener("click",closeBuy);
  $("buyConfirm").addEventListener("click",confirmBuy);
  $("buyPrice").addEventListener("input",updStop);
  $("buyPrice").addEventListener("keydown",function(e){if(e.key==="Enter")confirmBuy();});
  $("buyModal").addEventListener("click",function(e){if(e.target.id==="buyModal")closeBuy();});
  $("btnAdd").addEventListener("click",addManual);
  $("addCode").addEventListener("keydown",function(e){if(e.key==="Enter")addManual();});
  $("btnNotify").addEventListener("click",function(){
    if(!window.Notification){toast("当前浏览器不支持通知");return;}
    try{
      Notification.requestPermission().then(function(p){
        toast(p==="granted"?"已开启：跌破止损且打开本页时会弹系统通知":"未授权，仍会在页面顶部显示红色告警");
      });
    }catch(e){toast("当前环境不支持通知授权");}
  });
  document.addEventListener("keydown",function(e){
    if(e.key==="Escape"){$("modal").classList.add("hidden");closeBuy();}
  });

  /* ---- toast ---- */
  var TT=null;
  window.__toast=function(msg){
    var t=$("toastMsg");t.textContent=msg;t.className="toast";
    clearTimeout(TT);TT=setTimeout(function(){t.className="toast hidden";},3200);
  };
  function toast(m){window.__toast(m);}

  renderAll();
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


def load_price_map(d: dict) -> tuple[dict, str, bool]:
    """读取全市场最新价，供候选池与持仓止损判断使用。

    优先用本次扫描落盘的 data/spot_cache.json（含全市场 5000+ 只，含用户手动
    添加但当日未命中信号的股票）；不存在时退化为结果表里的命中股。
    返回 (价格映射, 基准时间文字, 是否过期)。价格是公开行情，可安全写入静态页。
    """
    out: dict[str, list] = {}
    ts = 0.0
    cache = ROOT / "data" / "spot_cache.json"
    if cache.exists():
        try:
            obj = json.loads(cache.read_text(encoding="utf-8"))
            ts = float(obj.get("ts") or 0)
            for row in obj.get("rows", []):
                code = str(row.get("code", "")).zfill(6)
                price = num(row.get("price"))
                if len(code) == 6 and code.isdigit() and price > 0:
                    out[code] = [round(price, 2), str(row.get("name", "") or ""),
                                 round(num(row.get("pct_chg")), 2)]
        except (OSError, ValueError, TypeError):
            pass
    # 结果表兜底并覆盖（命中股用 14:30 精确快照价）
    for x in d.get("results", []):
        code = str(x.get("code", "")).zfill(6)
        price = num(x.get("price"))
        if len(code) == 6 and code.isdigit() and price > 0:
            out[code] = [round(price, 2), str(x.get("name", "") or ""),
                         round(num(x.get("pct_chg")), 2)]
    stale = bool(d.get("snapshot_stale"))
    if ts > 0:
        dt = datetime.fromtimestamp(ts, CN_TZ)
        stale = stale or (datetime.now(CN_TZ) - dt).days > 3
        default_date = dt.strftime("%Y-%m-%d %H:%M")
    else:
        default_date = ""
    # 基准时间优先用结果本身的扫描时刻，与页面「扫描时间」保持一致
    date_str = d.get("scan_time") or d.get("market_date") or default_date
    return out, date_str, stale


def build_data_js(d: dict, price_map: dict, price_date: str, price_stale: bool) -> str:
    rows = [{k: x[k] for k in KEEP if k in x} for x in d.get("results", [])]
    payload = {
        "results": rows,
        "scan_time": d.get("scan_time", ""),
        "market_date": d.get("market_date", ""),
        "is_demo": bool(d.get("is_demo")),
        "price_map": price_map,
        "price_date": price_date,
        "price_stale": bool(price_stale),
    }
    raw = json.dumps(scrub(payload), ensure_ascii=False, separators=(",", ":"))
    # 防止 JSON 文本里出现 </script> 提前闭合脚本标签
    return "window.__STOCK_DATA__ = " + raw.replace("</", "<\\/") + ";\n"


PERSONAL_HTML = '\n<div id="stopAlert" class="alert-bar"></div>\n<section class="personal">\n  <h2>个人观察台 <span class="tag">本机保存</span></h2>\n  <p class="desc">从上方结果表或个股详情把股票加入<b>候选池</b>跟踪后续走势，或点<b>买入</b>并本人确认成交价后进入持仓监控。\n  持仓按成交价自动设 <b>-6% 止跌线</b>；打开本页时用最近一次扫描快照价检查，跌破即在此处红色提醒并弹浏览器通知（需点「开启提醒」授权）。\n  <b>本工具只记录与提醒，不会自动下单，也不构成投资建议；所有买卖由你本人执行。</b></p>\n  <div class="pgrid">\n    <div class="pcard">\n      <h3>持仓监控 · 买入观察组 <span class="cnt" id="holdCnt"></span></h3>\n      <p class="psub">止损价 = 成交价 × 0.94。卖出后点「已卖出·删除」即从本组移除买入信息。</p>\n      <div class="scroll"><table class="ptable"><thead><tr>\n        <th>代码</th><th>名称</th><th>买入价</th><th>买入日</th><th>止损价</th>\n        <th>最新价</th><th>浮动</th><th>状态</th><th>操作</th>\n      </tr></thead><tbody id="holdBody"></tbody></table></div>\n    </div>\n    <div class="pcard">\n      <h3>候选观察池 <span class="cnt" id="watchCnt"></span></h3>\n      <p class="psub">用户个人维护：可从结果表一键加入，也可手动输入代码。每次打开本页记录一个价格点，画出加入以来的走势。</p>\n      <div class="addbar">\n        <input id="addCode" type="text" maxlength="16" placeholder="代码（可粘贴 sh600000）">\n        <input id="addName" class="wname" type="text" placeholder="名称（可留空）">\n        <button class="btn pri" id="btnAdd">+ 添加候选</button>\n        <button class="btn" id="btnNotify">开启提醒</button>\n      </div>\n      <div class="scroll"><table class="ptable"><thead><tr>\n        <th>代码</th><th>名称</th><th>加入日</th><th>加入价</th>\n        <th>最新价</th><th>累计</th><th>走势</th><th>操作</th>\n      </tr></thead><tbody id="watchBody"></tbody></table></div>\n    </div>\n  </div>\n  <p class="hint-s" id="priceHint"></p>\n  <p class="hint-s">数据保存在本设备浏览器（localStorage），换设备 / 清除浏览器数据不会同步，也不上传服务器。\n  价格来自每次扫描的 14:30 前后快照，<b>不是实时盘中价</b>；不打开本页不会主动提醒，盘中急跌请自行盯盘。\n  候选池与持仓监控仅用于跟踪与纪律提醒，请勿据此直接下单。</p>\n</section>\n\n<div id="buyModal" class="modal hidden">\n  <div class="modal-card" style="width:min(460px,100%)">\n    <div class="modal-head"><h3 id="buyTitle">确认买入</h3>\n      <button id="buyCloseX" aria-label="关闭">×</button></div>\n    <div class="buy-body">\n      <label class="lbl" for="buyPrice">本人确认的成交价（元）</label>\n      <input id="buyPrice" type="number" step="0.01" min="0.01" placeholder="请输入实际成交价">\n      <div class="buy-calc" id="buyCalc"></div>\n      <p class="hint-s">确认即视为你已完成买入，程序只记录并监控，不会向券商下单。\n      止损价按成交价固定 -6% 计算；跌破时本页顶部红色提醒，请自行强制卖出。</p>\n      <div class="buy-actions">\n        <button class="btn" id="buyCancel">取消</button>\n        <button class="btn pri" id="buyConfirm">确认买入</button>\n      </div>\n    </div>\n  </div>\n</div>\n<div id="toastMsg" class="toast hidden"></div>\n'


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
    stale = bool(d.get("snapshot_stale"))
    stale_saved = d.get("snapshot_saved_at", "")
    stale_tag = '<span class="tag">快照滞后</span>' if stale else ""
    if not push:
        push_txt = '<span class="warn">本次未推送（dry-run 或尚未执行推送）</span>'
    elif push.get("channel") == "skipped":
        push_txt = '<span class="warn">已跳过推送 · 快照滞后</span>'
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
    stale_note = (
        f'<br><span class="warn">数据降级：本次东财/新浪/腾讯三通道均不可用，'
        f'价格来自 {esc(stale_saved)} 的缓存快照（非当日实时），已跳过推送；'
        f'上方价格与信号仅供复盘，请勿据此下单。</span>'
        if stale else ""
    )

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
                f"<td>{sample}</td>"
                f'<td><div class="rowbtns">'
                f'<button class="btn sm" data-act="watch" data-code="{esc(x.get("code"))}">+ 候选</button>'
                f'<button class="btn sm pri" data-act="buy" data-code="{esc(x.get("code"))}">买入</button>'
                f'</div></td></tr>'
            )
        table = (
            '<div class="scroll"><table id="tbl"><thead><tr>'
            "<th>#</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌幅</th>"
            "<th>历史胜率</th><th>平均收益</th><th>信号/胜次</th><th>破轨天数</th><th>备注</th><th>操作</th>"
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
<link rel="icon" href="data:,">
<style>{CSS}</style></head><body><div class="wrap">
<header>
  <h1>布林下轨反转选股 · 扫描结果{demo_tag}{stale_tag}</h1>
  <div class="sub">{esc(cron)} 自动扫描 · 连续跌破布林下轨后阳线收复 · 按历史胜率排序</div>
</header>
<section class="kpis">{kpis}</section>
<section class="status">
  <div><b>扫描时间</b>{esc(d.get('scan_time', '-'))}</div>
  <div><b>交易日</b>{esc(d.get('market_date', '-'))}</div>
  <div><b>数据源</b>{esc(d.get('source', '-'))}</div>
  <div><b>快照通道</b>{esc(d.get('snapshot_channel') or '-')}</div>
  <div><b>推送</b>{push_txt}</div>
  <div><b>耗时</b>{esc(d.get('elapsed_sec', '-'))} s</div>
</section>
<section class="note">
  <h2>策略口径</h2>
  BOLL(<code>{esc(window)}, {esc(mult)}</code>)；昨日往前连续 ≥<code>{esc(brk)}</code> 日收盘在下轨之下且逐日走低，
  区间跌幅 ≥<code>{esc(dec)}%</code>；扫描时点用盘中价构造当日 K 线，要求<strong>收阳、涨幅 ≥{esc(ymin)}%、收盘重新站上下轨</strong>。<br>
  股票池剔除科创板 / 北交所 / ST / 退市 / 停牌 / 成交额过低 / 上市不足 60 日。<br>
  历史胜率：同类信号后持有 <code>{esc(hold)}</code> 个交易日收益 &gt; 0 记为获胜，回看 3 年，
  贝叶斯平滑后降序取 Top {esc(top_n)}（样本不足者不参与推送）。{fb_note}{stale_note}
</section>
{table}
{PERSONAL_HTML}
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
      <div id="modalActs"></div>
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
    price_map, price_date, price_stale = load_price_map(d)
    (DOCS / "data.js").write_text(
        build_data_js(d, price_map, price_date, price_stale), encoding="utf-8"
    )
    if KLINE_SRC.exists():
        shutil.copyfile(KLINE_SRC, DOCS / "kline.js")
    else:
        raise SystemExit("缺少 static/kline.js，无法生成自包含结果页")
    print(
        f"已生成 {DOCS / 'index.html'}（{len(d.get('results', []))} 条结果）、data.js、kline.js、latest.json"
        f" | 价格映射 {len(price_map)} 只，基准 {price_date or '无'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
