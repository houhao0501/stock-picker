/* 日 K 图表组件（自包含、无外部依赖）
 * 蜡烛图 + BOLL + MA + 成交量 + MACD + 十字光标，红涨绿跌（A 股习惯）。
 * 用法：KLineChart.render(container, series, opts)
 * series 由 core/indicators.py::build_kline_series 生成。
 */
(function () {
  "use strict";

  var C = {
    bg: "#0f141c", grid: "#232c3b", line: "#2b3546", txt: "#8b98ab", txt2: "#c9d4e3",
    up: "#f0524d", down: "#2ea043", gold: "#f2b544", blue: "#4c8dff",
    lower: "#22c07a", ma5: "#f2b544", ma10: "#b48cf2", ma20: "#39b7e8",
    dif: "#e6edf3", dea: "#f2b544", mid: "#8697b0"
  };

  var STYLE = [
    ".kl{font:12px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif;color:#c9d4e3}",
    ".kl-bar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;margin-bottom:9px;font-size:11px;color:#8b98ab}",
    ".kl-group{display:flex;align-items:center;gap:5px}",
    ".kl-group>span{margin-right:3px}",
    ".kl-btn{background:#1b2330;border:1px solid #2b3546;color:#8b98ab;border-radius:5px;",
    "padding:3px 9px;cursor:pointer;font-size:11px;transition:.15s}",
    ".kl-btn:hover{border-color:#4c8dff;color:#e6edf3}",
    ".kl-btn.on{background:rgba(76,141,255,.18);border-color:#4c8dff;color:#e6edf3}",
    ".kl-chk{display:inline-flex;align-items:center;gap:4px;cursor:pointer;user-select:none}",
    ".kl-chk input{accent-color:#4c8dff;cursor:pointer;margin:0}",
    ".kl-legend{display:flex;flex-wrap:wrap;gap:5px 14px;font-size:11px;color:#8b98ab;margin:0 0 7px 2px;min-height:17px}",
    ".kl-legend i{display:inline-block;width:9px;height:2px;vertical-align:middle;margin-right:5px;border-radius:1px}",
    ".kl-legend b{color:#c9d4e3;font-weight:600;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}",
    ".kl-canvas{position:relative;background:#0f141c;border:1px solid #2b3546;border-radius:8px;overflow:hidden}",
    ".kl-canvas svg{width:100%;height:auto;display:block}",
    ".kl-tip{position:absolute;pointer-events:none;display:none;z-index:6;min-width:258px;",
    "background:rgba(15,21,31,.97);border:1px solid #3a465b;border-radius:8px;padding:10px 12px;",
    "box-shadow:0 12px 34px rgba(0,0,0,.55);font-size:11px;color:#c9d4e3}",
    ".kl-tip .h{font-weight:700;font-size:12px;margin-bottom:7px;padding-bottom:6px;border-bottom:1px solid #2b3546}",
    ".kl-tip .r{display:flex;justify-content:space-between;gap:16px;line-height:1.75;white-space:nowrap}",
    ".kl-tip .r span:first-child{color:#8b98ab}",
    ".kl-tip .r b{font-weight:600;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}",
    ".kl-tip .sec{margin-top:6px;padding-top:5px;border-top:1px dashed #2b3546}",
    ".kl-hint{margin-top:7px;color:#66768f;font-size:10px;line-height:1.6}",
    ".kl-up{color:#f0524d}.kl-down{color:#2ea043}.kl-flat{color:#c9d4e3}"
  ].join("");

  function injectStyle() {
    if (document.getElementById("kl-style")) return;
    var s = document.createElement("style");
    s.id = "kl-style";
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  function ok(v) { return v != null && v !== "" && Number.isFinite(+v); }
  function n2(v) { return ok(v) ? (+v).toFixed(2) : "—"; }
  function n3(v) { return ok(v) ? (+v).toFixed(3) : "—"; }
  function signed(v, digits) {
    if (!ok(v)) return "—";
    var x = +v, d = digits == null ? 2 : digits;
    return (x >= 0 ? "+" : "") + x.toFixed(d) + "%";
  }
  function colorOf(v) { return +v > 0 ? C.up : +v < 0 ? C.down : C.txt2; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtVol(v) {
    if (!ok(v)) return "—";
    var x = +v;
    if (x >= 1e8) return (x / 1e8).toFixed(2) + " 亿手";
    if (x >= 1e4) return (x / 1e4).toFixed(2) + " 万手";
    return Math.round(x) + " 手";
  }
  function fmtAmt(v) {
    if (!ok(v)) return "—";
    var x = +v;
    if (x >= 1e8) return (x / 1e8).toFixed(2) + " 亿";
    if (x >= 1e4) return (x / 1e4).toFixed(0) + " 万";
    return Math.round(x) + " 元";
  }

  function normalize(s) {
    var dates = s.dates || [], n = dates.length;
    var close = s.close || [];
    function arr(k, fallback) {
      var a = s[k];
      if (Array.isArray(a) && a.length === n) return a;
      return dates.map(function (_, i) { return fallback == null ? null : (typeof fallback === "function" ? fallback(i) : fallback); });
    }
    return {
      n: n, dates: dates,
      open: arr("open", function (i) { return close[i]; }),
      high: arr("high", function (i) { return close[i]; }),
      low: arr("low", function (i) { return close[i]; }),
      close: close,
      volume: arr("volume", 0), amount: arr("amount", 0),
      upper: arr("upper", null), mid: arr("mid", null), lower: arr("lower", null),
      ma5: arr("ma5", null), ma10: arr("ma10", null), ma20: arr("ma20", null),
      vol_ma5: arr("vol_ma5", null), dif: arr("dif", null), dea: arr("dea", null), macd: arr("macd", null),
      hasOHLC: Array.isArray(s.open) && s.open.length === n
    };
  }

  function mkPath(vals, X, Y) {
    var d = "", pen = false;
    for (var i = 0; i < vals.length; i++) {
      if (!ok(vals[i])) { pen = false; continue; }
      d += (pen ? "L" : "M") + X(i).toFixed(1) + " " + Y(vals[i]).toFixed(1) + " ";
      pen = true;
    }
    return d.trim();
  }

  function render(el, series, opts) {
    injectStyle();
    if (!el) return;
    if (el._klKeyHandler) document.removeEventListener("keydown", el._klKeyHandler);
    opts = opts || {};
    var A = normalize(series || {});
    if (A.n < 2) { el.innerHTML = '<div style="padding:36px;text-align:center;color:#8b98ab">无走势数据</div>'; return; }

    var state = {
      view: Math.min(opts.view || 60, A.n),
      boll: opts.boll !== false, ma: opts.ma !== false,
      vol: opts.vol !== false, macd: opts.macd !== false,
      cursor: -1
    };

    el.innerHTML =
      '<div class="kl">' +
        '<div class="kl-bar">' +
          '<div class="kl-group"><span>周期</span>' +
            [30, 60, 90].map(function (v) { return '<button type="button" class="kl-btn" data-view="' + v + '">' + v + ' 日</button>'; }).join("") +
          '</div>' +
          '<div class="kl-group"><span>指标</span>' +
            '<label class="kl-chk"><input type="checkbox" data-ind="boll"' + (state.boll ? " checked" : "") + '>BOLL</label>' +
            '<label class="kl-chk"><input type="checkbox" data-ind="ma"' + (state.ma ? " checked" : "") + '>MA</label>' +
            '<label class="kl-chk"><input type="checkbox" data-ind="vol"' + (state.vol ? " checked" : "") + '>成交量</label>' +
            '<label class="kl-chk"><input type="checkbox" data-ind="macd"' + (state.macd ? " checked" : "") + '>MACD</label>' +
          '</div>' +
        '</div>' +
        '<div class="kl-legend"></div>' +
        '<div class="kl-canvas"><svg viewBox="0 0 920 500" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet"></svg><div class="kl-tip"></div></div>' +
        '<div class="kl-hint">滚轮缩放 · 鼠标移动查看逐日明细 · ← → 切换光标 · 阳线红 / 阴线绿（A 股习惯）· 最后一根为 14:30 盘中快照，非收盘价</div>' +
      '</div>';

    var svg = el.querySelector("svg");
    var canvas = el.querySelector(".kl-canvas");
    var tip = el.querySelector(".kl-tip");
    var legend = el.querySelector(".kl-legend");
    var W = 920, H = 500, PL = 8, PR = 62, PW = W - PL - PR;
    var MAIN_T = 28, AXIS_Y = 476, BOTTOM = 468;

    function sliceAll(start, count) {
      var o = {};
      Object.keys(A).forEach(function (k) {
        if (Array.isArray(A[k]) && A[k].length === A.n) o[k] = A[k].slice(start, start + count);
        else o[k] = A[k];
      });
      o.n = count;
      return o;
    }

    function buildLayout() {
      var panes = [], weights = [];
      if (state.vol) weights.push({ key: "vol", w: 0.17 });
      if (state.macd) weights.push({ key: "macd", w: 0.17 });
      var subTotal = weights.reduce(function (a, x) { return a + x.w; }, 0);
      var gap = 14, avail = BOTTOM - MAIN_T - gap * weights.length;
      var mainH = avail * (1 - subTotal);
      panes.push({ key: "main", t: MAIN_T, h: mainH });
      var y = MAIN_T + mainH + gap;
      weights.forEach(function (x) {
        var h = avail * x.w;
        panes.push({ key: x.key, t: y, h: h });
        y += h + gap;
      });
      var map = {};
      panes.forEach(function (p) { map[p.key] = p; });
      return map;
    }

    function draw() {
      var view = Math.min(state.view, A.n), start = A.n - view, d = sliceAll(start, view);
      var L = buildLayout(), main = L.main;
      var X = function (i) { return PL + (i + 0.5) * PW / view; };
      var cw = Math.max(1.6, Math.min(11, PW / view * 0.62));

      // 主图价格范围
      var vals = [];
      ["high", "low", "close", "open"].forEach(function (k) {
        d[k].forEach(function (v) { if (ok(v)) vals.push(+v); });
      });
      if (state.boll) ["upper", "lower"].forEach(function (k) {
        d[k].forEach(function (v) { if (ok(v)) vals.push(+v); });
      });
      if (!vals.length) vals = [0, 1];
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
      var pad = (hi - lo) * 0.06 || hi * 0.02 || 1;
      lo -= pad; hi += pad;
      var Y = function (v) { return main.t + (hi - v) / (hi - lo) * main.h; };

      var out = [];
      out.push('<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="' + C.bg + '"/>');

      // 网格 + 右侧价格刻度（同花顺风格，价格在右）
      for (var g = 0; g <= 4; g++) {
        var v = hi - g * (hi - lo) / 4, y = Y(v);
        out.push('<line x1="' + PL + '" y1="' + y.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + y.toFixed(1) + '" stroke="' + C.grid + '" stroke-width="1" stroke-dasharray="3 4"/>');
        out.push('<text x="' + (W - PR + 7) + '" y="' + (y + 3.5).toFixed(1) + '" fill="' + C.txt + '" font-size="10" font-family="monospace">' + v.toFixed(2) + '</text>');
      }
      var step = Math.max(1, Math.round(view / 7));
      for (var i2 = 0; i2 < view; i2 += step) {
        out.push('<line x1="' + X(i2).toFixed(1) + '" y1="' + MAIN_T + '" x2="' + X(i2).toFixed(1) + '" y2="' + BOTTOM + '" stroke="' + C.grid + '" stroke-width="1" stroke-dasharray="2 6" opacity=".55"/>');
      }

      // 破轨区间底色（收盘 <= 下轨）
      if (state.boll) {
        for (var i3 = 0; i3 < view; i3++) {
          if (ok(d.lower[i3]) && ok(d.close[i3]) && +d.close[i3] <= +d.lower[i3]) {
            out.push('<rect x="' + (X(i3) - cw / 2 - 1).toFixed(1) + '" y="' + main.t.toFixed(1) + '" width="' + (cw + 2).toFixed(1) + '" height="' + main.h.toFixed(1) + '" fill="rgba(240,82,77,.055)"/>');
          }
        }
      }

      // BOLL 带
      if (state.boll) {
        var upPts = [], loPts = [];
        for (var i4 = 0; i4 < view; i4++) {
          if (ok(d.upper[i4])) upPts.push(X(i4).toFixed(1) + "," + Y(d.upper[i4]).toFixed(1));
          if (ok(d.lower[i4])) loPts.push(X(i4).toFixed(1) + "," + Y(d.lower[i4]).toFixed(1));
        }
        loPts.reverse();
        if (upPts.length > 1) out.push('<polygon points="' + upPts.concat(loPts).join(" ") + '" fill="rgba(76,141,255,.075)"/>');
        out.push('<path d="' + mkPath(d.upper, X, Y) + '" fill="none" stroke="' + C.blue + '" stroke-width="1.2" opacity=".85"/>');
        out.push('<path d="' + mkPath(d.lower, X, Y) + '" fill="none" stroke="' + C.lower + '" stroke-width="1.2" opacity=".9"/>');
        out.push('<path d="' + mkPath(d.mid, X, Y) + '" fill="none" stroke="' + C.mid + '" stroke-width="1.1" stroke-dasharray="5 4" opacity=".75"/>');
      }

      // MA 均线
      if (state.ma) {
        [["ma5", C.ma5], ["ma10", C.ma10], ["ma20", C.ma20]].forEach(function (p) {
          out.push('<path d="' + mkPath(d[p[0]], X, Y) + '" fill="none" stroke="' + p[1] + '" stroke-width="1.25" opacity=".95"/>');
        });
      }

      // K 线蜡烛
      for (var i5 = 0; i5 < view; i5++) {
        var o = +d.open[i5], c = +d.close[i5], h = +d.high[i5], l = +d.low[i5];
        if (![o, c, h, l].every(ok)) continue;
        var rising = c >= o, col = rising ? C.up : C.down;
        var x = X(i5).toFixed(1);
        out.push('<line x1="' + x + '" y1="' + Y(h).toFixed(1) + '" x2="' + x + '" y2="' + Y(l).toFixed(1) + '" stroke="' + col + '" stroke-width="1"/>');
        var yt = Y(Math.max(o, c)), yb = Y(Math.min(o, c));
        out.push('<rect x="' + (X(i5) - cw / 2).toFixed(1) + '" y="' + yt.toFixed(1) + '" width="' + cw.toFixed(1) + '" height="' + Math.max(1.2, yb - yt).toFixed(1) + '" fill="' + col + '" stroke="' + col + '" stroke-width=".6"/>');
      }

      // 今日信号竖线 + 固定在主图顶部的标签（避免遮挡蜡烛）
      var lastX = X(view - 1);
      out.push('<line x1="' + lastX.toFixed(1) + '" y1="' + MAIN_T + '" x2="' + lastX.toFixed(1) + '" y2="' + BOTTOM + '" stroke="' + C.gold + '" stroke-width="1" stroke-dasharray="3 3" opacity=".65"/>');
      var tagW = 50, tagX = Math.min(W - PR - tagW, Math.max(PL + 2, lastX - tagW / 2));
      out.push('<rect x="' + tagX.toFixed(1) + '" y="' + (MAIN_T + 2) + '" width="' + tagW + '" height="16" rx="3" fill="#3a2f14" stroke="' + C.gold + '" stroke-width=".8"/>');
      out.push('<text x="' + (tagX + tagW / 2).toFixed(1) + '" y="' + (MAIN_T + 13.5) + '" fill="' + C.gold + '" font-size="9.5" text-anchor="middle">★ 信号日</text>');

      // 右侧价格轴高亮最新价（同花顺风格）
      var lc = +d.close[view - 1], ly = Y(lc);
      var lcColor = lc >= +d.open[view - 1] ? C.up : C.down;
      out.push('<rect x="' + (W - PR + 2) + '" y="' + (ly - 8).toFixed(1) + '" width="' + (PR - 5) + '" height="16" rx="2" fill="' + lcColor + '"/>');
      out.push('<text x="' + (W - PR + 6) + '" y="' + (ly + 3.5).toFixed(1) + '" fill="#fff" font-size="10.5" font-family="monospace" font-weight="bold">' + lc.toFixed(2) + '</text>');

      // 成交量副图
      if (state.vol && L.vol) {
        var vp = L.vol, maxV = 0;
        d.volume.forEach(function (v) { if (ok(v)) maxV = Math.max(maxV, +v); });
        d.vol_ma5.forEach(function (v) { if (ok(v)) maxV = Math.max(maxV, +v); });
        maxV = maxV || 1;
        var VY = function (v) { return vp.t + vp.h - (+v / maxV) * vp.h * 0.94; };
        out.push('<text x="' + (PL + 2) + '" y="' + (vp.t + 10).toFixed(1) + '" fill="' + C.txt + '" font-size="10">VOL</text>');
        out.push('<text x="' + (W - PR + 7) + '" y="' + (vp.t + 10).toFixed(1) + '" fill="' + C.txt + '" font-size="9.5">' + fmtVol(maxV) + '</text>');
        out.push('<line x1="' + PL + '" y1="' + (vp.t + vp.h).toFixed(1) + '" x2="' + (W - PR) + '" y2="' + (vp.t + vp.h).toFixed(1) + '" stroke="' + C.grid + '"/>');
        for (var i6 = 0; i6 < view; i6++) {
          var vv = +d.volume[i6] || 0, rising6 = +d.close[i6] >= +d.open[i6];
          out.push('<rect x="' + (X(i6) - cw / 2).toFixed(1) + '" y="' + VY(vv).toFixed(1) + '" width="' + cw.toFixed(1) + '" height="' + Math.max(0.5, vp.t + vp.h - VY(vv)).toFixed(1) + '" fill="' + (rising6 ? C.up : C.down) + '" opacity=".78"/>');
        }
        var vmaPath = d.vol_ma5.map(function (v, i) {
          return ok(v) ? (i === 0 ? "M" : "L") + X(i).toFixed(1) + " " + VY(v).toFixed(1) : "";
        }).filter(Boolean).join(" ");
        if (vmaPath) out.push('<path d="' + vmaPath + '" fill="none" stroke="' + C.gold + '" stroke-width="1.1" opacity=".85"/>');
      }

      // MACD 副图
      if (state.macd && L.macd) {
        var mp = L.macd, mx = 0;
        ["dif", "dea", "macd"].forEach(function (k) { d[k].forEach(function (v) { if (ok(v)) mx = Math.max(mx, Math.abs(+v)); }); });
        mx = mx || 1;
        var MY = function (v) { return mp.t + mp.h / 2 - (+v / mx) * (mp.h / 2) * 0.9; };
        out.push('<text x="' + (PL + 2) + '" y="' + (mp.t + 10).toFixed(1) + '" fill="' + C.txt + '" font-size="10">MACD(12,26,9)</text>');
        out.push('<line x1="' + PL + '" y1="' + MY(0).toFixed(1) + '" x2="' + (W - PR) + '" y2="' + MY(0).toFixed(1) + '" stroke="' + C.line + '"/>');
        for (var i7 = 0; i7 < view; i7++) {
          var m = +d.macd[i7];
          if (!ok(m)) continue;
          var y0 = MY(0), y1 = MY(m), col7 = m >= 0 ? C.up : C.down;
          out.push('<rect x="' + (X(i7) - cw / 2).toFixed(1) + '" y="' + Math.min(y0, y1).toFixed(1) + '" width="' + cw.toFixed(1) + '" height="' + Math.max(0.6, Math.abs(y1 - y0)).toFixed(1) + '" fill="' + col7 + '" opacity=".8"/>');
        }
        out.push('<path d="' + mkPath(d.dif, X, MY) + '" fill="none" stroke="' + C.dif + '" stroke-width="1.15"/>');
        out.push('<path d="' + mkPath(d.dea, X, MY) + '" fill="none" stroke="' + C.dea + '" stroke-width="1.15"/>');
      }

      // 时间轴
      var ticks = [];
      for (var t = 0; t < view; t += Math.max(1, Math.round(view / 6))) ticks.push(t);
      if (ticks[ticks.length - 1] !== view - 1) ticks.push(view - 1);
      ticks.forEach(function (i8) {
        var label = String(d.dates[i8] || "").slice(5);
        var anchor = i8 === 0 ? "start" : i8 === view - 1 ? "end" : "middle";
        out.push('<text x="' + X(i8).toFixed(1) + '" y="' + AXIS_Y + '" fill="' + C.txt + '" font-size="10" text-anchor="' + anchor + '">' + esc(label) + '</text>');
      });
      out.push('<line x1="' + PL + '" y1="' + (BOTTOM + 3) + '" x2="' + (W - PR) + '" y2="' + (BOTTOM + 3) + '" stroke="' + C.line + '"/>');

      // 十字光标层（默认隐藏）
      out.push('<g class="kl-cross" style="display:none">' +
        '<rect class="kl-cross-bg" y="' + MAIN_T + '" width="' + cw.toFixed(1) + '" height="' + (BOTTOM - MAIN_T).toFixed(1) + '" fill="rgba(255,255,255,.045)"/>' +
        '<line class="kl-cross-v" y1="' + MAIN_T + '" y2="' + BOTTOM + '" stroke="#7d8ca3" stroke-width="1" stroke-dasharray="4 3"/>' +
        '<line class="kl-cross-h" x1="' + PL + '" x2="' + (W - PR) + '" stroke="#7d8ca3" stroke-width="1" stroke-dasharray="4 3"/>' +
        '</g>');

      svg.innerHTML = out.join("");
      state._d = d; state._X = X; state._Y = Y; state._cw = cw; state._view = view;
      state._layout = L; state._start = start;
      updateLegend(state.cursor >= 0 ? state.cursor : view - 1);
      syncButtons();
      if (state.cursor >= 0) positionCross(Math.min(state.cursor, view - 1));
    }

    function syncButtons() {
      el.querySelectorAll("[data-view]").forEach(function (b) {
        b.classList.toggle("on", +b.dataset.view === state.view);
      });
    }

    function updateLegend(i) {
      var d = state._d; if (!d || !d.dates[i]) return;
      var items = [];
      if (state.ma) items.push(
        [C.ma5, "MA5", n2(d.ma5[i])], [C.ma10, "MA10", n2(d.ma10[i])], [C.ma20, "MA20", n2(d.ma20[i])]
      );
      if (state.boll) items.push(
        [C.blue, "UPPER", n2(d.upper[i])], [C.mid, "MID", n2(d.mid[i])], [C.lower, "LOWER", n2(d.lower[i])]
      );
      if (state.macd) items.push(
        [C.dif, "DIF", n3(d.dif[i])], [C.dea, "DEA", n3(d.dea[i])], ["#9aa7ba", "MACD", n3(d.macd[i])]
      );
      legend.innerHTML = '<span style="color:#e6edf3">' + esc(d.dates[i]) + '</span>' +
        items.map(function (x) {
          return '<span><i style="background:' + x[0] + '"></i>' + x[1] + ' <b>' + x[2] + '</b></span>';
        }).join("");
    }

    function positionCross(i) {
      var cross = svg.querySelector(".kl-cross");
      if (!cross) return;
      var x = state._X(i), cw = state._cw;
      cross.style.display = "";
      cross.querySelector(".kl-cross-bg").setAttribute("x", (x - cw / 2 - 2).toFixed(1));
      cross.querySelector(".kl-cross-v").setAttribute("x1", x.toFixed(1));
      cross.querySelector(".kl-cross-v").setAttribute("x2", x.toFixed(1));
      updateLegend(i);
    }

    function showTip(i, clientX, clientY) {
      var d = state._d, n = state._view;
      var c = +d.close[i], o = +d.open[i], h = +d.high[i], l = +d.low[i];
      var prev = i > 0 ? +d.close[i - 1] : o;
      var chg = prev ? c - prev : 0, chgPct = prev ? chg / prev * 100 : 0;
      var amp = prev ? (h - l) / prev * 100 : 0;
      var cc = colorOf(chgPct);
      var below = ok(d.lower[i]) && c <= +d.lower[i];
      var bollPos = (ok(d.upper[i]) && ok(d.lower[i]) && +d.upper[i] > +d.lower[i])
        ? (c - +d.lower[i]) / (+d.upper[i] - +d.lower[i]) * 100 : null;
      var row = function (k, v, cl) {
        return '<div class="r"><span>' + k + '</span><b' + (cl ? ' style="color:' + cl + '"' : "") + '>' + v + '</b></div>';
      };
      tip.innerHTML =
        '<div class="h">' + esc(d.dates[i]) + (i === n - 1 ? ' <span style="color:#f2b544;font-size:10px">14:30 盘中</span>' : "") + '</div>' +
        row("开盘", n2(o)) + row("最高", n2(h), C.up) + row("最低", n2(l), C.down) +
        row("收盘", n2(c), cc) +
        row("涨跌", (chg >= 0 ? "+" : "") + chg.toFixed(2) + "  " + signed(chgPct), cc) +
        row("振幅", amp.toFixed(2) + "%") +
        '<div class="sec"></div>' +
        row("成交量", fmtVol(d.volume[i])) + row("成交额", fmtAmt(d.amount[i])) +
        row("VOL MA5", fmtVol(d.vol_ma5[i])) +
        '<div class="sec"></div>' +
        row("MA5 / 10 / 20", n2(d.ma5[i]) + " / " + n2(d.ma10[i]) + " / " + n2(d.ma20[i])) +
        row("BOLL 上/中/下", n2(d.upper[i]) + " / " + n2(d.mid[i]) + " / " + n2(d.lower[i])) +
        row("布林位置", bollPos == null ? "—" : bollPos.toFixed(1) + "%") +
        row("MACD DIF/DEA/柱", n3(d.dif[i]) + " / " + n3(d.dea[i]) + " / " + n3(d.macd[i])) +
        (below ? '<div class="sec" style="color:#f0524d">⚠ 当日收盘跌破布林下轨</div>' : "") +
        (i === n - 1 ? '<div class="sec" style="color:#f2b544">★ 今日阳线收复信号</div>' : "");
      tip.style.display = "block";
      var r = canvas.getBoundingClientRect();
      var px = clientX - r.left, py = clientY - r.top;
      var tw = tip.offsetWidth, th = tip.offsetHeight;
      tip.style.left = (px + tw + 26 > r.width ? Math.max(4, px - tw - 14) : px + 14) + "px";
      tip.style.top = Math.max(4, Math.min(r.height - th - 4, py - 14)) + "px";
    }

    function hideCursor() {
      var cross = svg.querySelector(".kl-cross");
      if (cross) cross.style.display = "none";
      tip.style.display = "none";
      state.cursor = -1;
      updateLegend(state._view - 1);
    }

    function moveTo(i, clientX, clientY) {
      state.cursor = Math.max(0, Math.min(state._view - 1, i));
      positionCross(state.cursor);
      showTip(state.cursor, clientX, clientY);
    }

    canvas.addEventListener("mousemove", function (e) {
      var r = canvas.getBoundingClientRect();
      var px = (e.clientX - r.left) * (W / r.width);
      var py = (e.clientY - r.top) * (W / r.width);
      if (px < PL || px > W - PR) { hideCursor(); return; }
      var i = Math.round((px - PL) / (PW / state._view) - 0.5);
      var cross = svg.querySelector(".kl-cross-h");
      if (cross && py >= MAIN_T && py <= BOTTOM) {
        cross.setAttribute("y1", py.toFixed(1));
        cross.setAttribute("y2", py.toFixed(1));
      }
      moveTo(i, e.clientX, e.clientY);
    });
    canvas.addEventListener("mouseleave", hideCursor);

    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      var next = state.view + (e.deltaY < 0 ? -10 : 10);
      state.view = Math.max(20, Math.min(90, Math.min(A.n, next)));
      state.cursor = -1;
      draw();
    }, { passive: false });

    el.querySelectorAll("[data-view]").forEach(function (b) {
      b.addEventListener("click", function () {
        state.view = Math.min(A.n, +b.dataset.view);
        state.cursor = -1;
        draw();
      });
    });
    el.querySelectorAll("[data-ind]").forEach(function (b) {
      b.addEventListener("change", function () {
        state[b.dataset.ind] = b.checked;
        state.cursor = -1;
        draw();
      });
    });

    el._klKeyHandler = function (e) {
      if (el.offsetParent === null) return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      var base = state.cursor < 0 ? state._view - 1 : state.cursor;
      var i = Math.max(0, Math.min(state._view - 1, base + (e.key === "ArrowLeft" ? -1 : 1)));
      var r = canvas.getBoundingClientRect();
      var cx = r.left + (state._X(i) / W) * r.width;
      var cy = r.top + r.height / 2;
      moveTo(i, cx, cy);
      e.preventDefault();
    };
    document.addEventListener("keydown", el._klKeyHandler);

    draw();
  }

  window.KLineChart = { render: render };
})();
