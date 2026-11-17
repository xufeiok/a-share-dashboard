/* ============================================================
 * 个股行情页 K 线渲染覆盖脚本（KLineChart v10.0.3 实现）
 * ------------------------------------------------------------
 * 功能：
 *  - K线/周期切换（日/周/月，映射 KLineChart Period）
 *  - 主图指标：MA/EMA/SAR/BOLL（内置）+ SuperTrend/VolumeDynamicVIDYA（自定义，后端计算）
 *  - 副图指标：VOL/MACD/KDJ/RSI/WR/MFI/OBV/CCI/DMI/TRIX/BOLL（内置）
 *              + XTR/BBTrendScore（自定义，后端计算）
 *  - 画线工具（内置 Overlay：直线/射线/水平垂直/斐波那契/文字标注等）
 *  - 主题切换（深色/浅色）、缩放复位、截图下载、全屏、指标参数设置
 * 数据源：
 *  - /api/stock/kline （K线，含 vol/amount）
 *  - /api/stock/indicators （SuperTrend/VolumeDynamicVIDYA/XTR/BBTrendScore，同频缓存）
 * 说明：
 *  - 覆盖 index.html 内联的原 ECharts StockKline 实现；
 *    本脚本在页面末尾加载，晚于原内联脚本，保证覆盖生效。
 *  - 自定义指标 calc 读取 indCache（与 K 线同频的后端指标缓存），
 *    后端无数据时返回空数组，图内不绘制，不影响其他指标。
 * 边界：
 *  - 数据接口异常时回调空数组，图表保持空态不报错。
 *  - 连续切换股票/周期用 loadToken 丢弃过期响应，避免数据错位。
 *  - 页面未加载 klinecharts 库时直接退出，不破坏原页面。
 * ============================================================ */
(function () {
  'use strict';
  if (!window.klinecharts) { console.warn('[SK-KC] klinecharts 库未加载，跳过覆盖'); return; }

  const API = window.__AK_API__ || 'http://127.0.0.1:5000/api';
  const RED = '#f23645', GREEN = '#089981', BLUE = '#2962ff', YELLOW = '#f0b90b', GRAY = '#787b86';

  /* 脚本加载指纹（供前端调试确认实际加载版本） */
  try { document.documentElement.setAttribute('data-sk-ver', '130'); } catch (e) { /* 忽略 */ }

  /* K线|信息 视图切换：文档级委托，不依赖绑定时机，保证 tab 点击始终生效 */
  document.addEventListener('click', function (e) {
    const t = e.target && e.target.closest ? e.target.closest('.sk-view-tab') : null;
    if (t && t.dataset && t.dataset.v && typeof setView === 'function') setView(t.dataset.v);
  });

  /* ---------------- 后端指标缓存（自定义指标数据源） ---------------- */
  const indCache = { data: null };
  function getInd() { return indCache.data || {}; }

  /* ---------------- 自定义指标注册 ---------------- */
  /* 注意：KLineChart v10 的 calc 必须返回「逐 K 线对象数组」（长度=bar 数，每元素 {figureKey: 数值}），
   * 不能返回 {key: [整段数组]} 的包数组格式（图表按 bar 索引取值会拿到数组，画不出线）。
   * v10 已知限制（实测确认）：多 figure 指标（≥2 条线）时——
   *   ① figures 里不能带 color 属性（会整条指标不渲染），颜色必须用 figure 的 styles 函数返回；
   *   ② calc 返回对象不能含 null 值（同样整条不渲染），无值字段要省略不 set（只 set 有值 key）。 */
  klinecharts.registerIndicator({
    name: 'SuperTrend', shortName: 'SuperTrend', series: 'line', calcParams: [10, 3],
    figures: [
      { key: 'up', title: 'ST-UP', type: 'line', styles: () => ({ color: '#26a69a' }) },
      { key: 'down', title: 'ST-DN', type: 'line', styles: () => ({ color: '#ef5350' }) }
    ],
    calc: function (dataList) {
      const st = getInd().supertrend || {};
      const up = st.up || [], down = st.down || [];
      return (dataList || []).map((_, i) => {
        const o = {};
        if (up[i] != null) o.up = up[i];
        if (down[i] != null) o.down = down[i];
        return o;
      });
    }
  });
  klinecharts.registerIndicator({
    name: 'VolumeDynamicVIDYA', shortName: 'VolumeDynamicVIDYA', series: 'line', calcParams: [10, 20, 200, 15, 2],
    figures: [
      { key: 'vidya', title: 'VolumeDynamicVIDYA', type: 'line', styles: () => ({ color: '#ab47bc' }) },
      { key: 'upper', title: '上轨', type: 'line', styles: () => ({ color: '#ce93d8' }) },
      { key: 'lower', title: '下轨', type: 'line', styles: () => ({ color: '#ce93d8' }) }
    ],
    calc: function (dataList) {
      const v = getInd().vidya || {};
      const vidya = v.vidya || [], upper = v.upper || [], lower = v.lower || [];
      return (dataList || []).map((_, i) => {
        const o = {};
        if (vidya[i] != null) o.vidya = vidya[i];
        if (upper[i] != null) o.upper = upper[i];
        if (lower[i] != null) o.lower = lower[i];
        return o;
      });
    }
  });
  klinecharts.registerIndicator({
    name: 'X_XTR', shortName: 'X_XTR', series: 'line', calcParams: [14, 9, 20, 2],
    figures: [
      { key: 'xtr', title: 'X_XTR', type: 'line', styles: () => ({ color: BLUE }) },
      { key: 'xtr_ma', title: 'X_XTR_MA', type: 'line', styles: () => ({ color: YELLOW }) },
      { key: 'top', title: '上轨', type: 'line', styles: () => ({ color: GRAY }) },
      { key: 'bot', title: '下轨', type: 'line', styles: () => ({ color: GRAY }) }
    ],
    calc: function (dataList) {
      const x = getInd().xtr || {};
      const xtr = x.xtr || [], xtr_ma = x.xtr_ma || [], top = x.top || [], bot = x.bot || [];
      return (dataList || []).map((_, i) => {
        const o = {};
        if (xtr[i] != null) o.xtr = xtr[i];
        if (xtr_ma[i] != null) o.xtr_ma = xtr_ma[i];
        if (top[i] != null) o.top = top[i];
        if (bot[i] != null) o.bot = bot[i];
        return o;
      });
    }
  });
  klinecharts.registerIndicator({
    name: 'BBTrendScore', shortName: 'BBTrendScore', series: 'line', calcParams: [50, 2, 5, 3, 60, 0.5, 0.3, 0.2],
    figures: [{ key: 'score', title: 'BBTrendScore', type: 'line', styles: () => ({ color: BLUE }) }],
    calc: function (dataList) {
      const b = getInd().bb_score || {};
      const score = b.score || [];
      return (dataList || []).map((_, i) => {
        const o = {};
        if (score[i] != null) o.score = score[i];
        return o;
      });
    }
  });
  /* UMA 三均线（主图，桥数据 ind.uma.ma{1,2,3}.value）——新增，indicators/uma.py */
  klinecharts.registerIndicator({
    name: 'UMA', shortName: 'UMA', series: 'line', calcParams: [20, 50, 100],
    figures: [
      { key: 'ma1', title: 'UMA1', type: 'line', styles: () => ({ color: '#2196F3' }) },
      { key: 'ma2', title: 'UMA2', type: 'line', styles: () => ({ color: '#4CAF50' }) },
      { key: 'ma3', title: 'UMA3', type: 'line', styles: () => ({ color: '#9E9E9E' }) }
    ],
    calc: function (dataList) {
      const u = getInd().uma || {};
      const g = (k) => (u[k] && u[k].value) || [];
      const ma1 = g('ma1'), ma2 = g('ma2'), ma3 = g('ma3');
      return (dataList || []).map((_, i) => {
        const o = {};
        if (ma1[i] != null) o.ma1 = ma1[i];
        if (ma2[i] != null) o.ma2 = ma2[i];
        if (ma3[i] != null) o.ma3 = ma3[i];
        return o;
      });
    }
  });
  /* 以下 4 个与内置同名注册，覆盖内置算法为 my_bt_indicator bt 版（indicators/EnhancedMACD.py 等）。
   * 菜单名不变，计算走 backtrader 桥；参数名/默认值与后端一致（见 PARAM_MAP）。 */
  klinecharts.registerIndicator({
    name: 'EnhancedMACD', shortName: 'EnhancedMACD', series: 'normal', calcParams: [12, 26, 9],
    figures: [
      /* 颜色对齐 indicators/EnhancedMACD.py 定义：DIF 橙 #ffa726 / DEA 蓝 #42a5f5 */
      { key: 'dif', title: 'DIF: ', type: 'line', styles: () => ({ color: '#ffa726' }) },
      { key: 'dea', title: 'DEA: ', type: 'line', styles: () => ({ color: '#42a5f5' }) },
      /* HIST 柱：逐柱颜色取桥 colors 数组（算法 9 色编码：红/粉/绿/浅蓝/深红/亮粉/深绿/亮青） */
      { key: 'hist', title: 'HIST: ', type: 'bar', baseValue: 0,
        styles: (p) => {
          const c = (p.data.current && p.data.current.histColor) || '#787b86';
          return { style: 'fill', color: c, borderColor: c };
        } }
    ],
    calc: function (dataList) {
      const m = getInd().macd || {};
      const dif = m.dif || [], dea = m.dea || [], hist = m.hist || [], colors = m.colors || [];
      return (dataList || []).map((_, i) => {
        const o = {};
        if (dif[i] != null) o.dif = dif[i];
        if (dea[i] != null) o.dea = dea[i];
        if (hist[i] != null) { o.hist = hist[i]; o.histColor = colors[i] || (hist[i] > 0 ? '#ef5350' : '#26a69a'); }
        return o;
      });
    }
  });
  klinecharts.registerIndicator({
    name: 'X_RSI', shortName: 'X_RSI', series: 'normal', calcParams: [14, 14],
    figures: [
      { key: 'rsi', title: 'RSI: ', type: 'line', styles: () => ({ color: '#ab47bc' }) },
      { key: 'rsi_ma', title: 'RSI_MA: ', type: 'line', styles: () => ({ color: '#f0b90b' }) }
    ],
    calc: function (dataList) {
      const r = getInd().rsi || {};
      const rsi = r.rsi || [], rsi_ma = r.rsi_ma || [];
      return (dataList || []).map((_, i) => {
        const o = {};
        if (rsi[i] != null) o.rsi = rsi[i];
        if (rsi_ma[i] != null) o.rsi_ma = rsi_ma[i];
        return o;
      });
    }
  });
  klinecharts.registerIndicator({
    name: 'X_KDJ', shortName: 'X_KDJ', series: 'normal', calcParams: [9, 3],
    figures: [
      { key: 'K', title: 'K: ', type: 'line', styles: () => ({ color: '#2962ff' }) },
      { key: 'D', title: 'D: ', type: 'line', styles: () => ({ color: '#f0b90b' }) },
      { key: 'J', title: 'J: ', type: 'line', styles: () => ({ color: '#787b86' }) }
    ],
    calc: function (dataList) {
      const kd = getInd().kdj || {};
      const K = kd.K || [], D = kd.D || [], J = kd.J || [];
      return (dataList || []).map((_, i) => {
        const o = {};
        if (K[i] != null) o.K = K[i];
        if (D[i] != null) o.D = D[i];
        if (J[i] != null) o.J = J[i];
        return o;
      });
    }
  });
  klinecharts.registerIndicator({
    name: 'X_OBV', shortName: 'X_OBV', series: 'normal', calcParams: [20],
    figures: [
      { key: 'obv', title: 'OBV: ', type: 'line', styles: () => ({ color: '#2962ff' }) },
      { key: 'obv_ma', title: 'OBV_MA: ', type: 'line', styles: () => ({ color: '#f0b90b' }) }
    ],
    calc: function (dataList) {
      const o = getInd().obv || {};
      const obv = o.obv || [], obv_ma = o.obv_ma || [];
      return (dataList || []).map((_, i) => {
        const oo = {};
        if (obv[i] != null) oo.obv = obv[i];
        if (obv_ma[i] != null) oo.obv_ma = obv_ma[i];
        return oo;
      });
    }
  });
  /* MFI（资金流量指标）：KLineChart 未内置，前端按与信息栏一致的公式注册（窗口滚动求和） */
  klinecharts.registerIndicator({
    name: 'MFI', shortName: 'MFI', series: 'normal', calcParams: [14],
    figures: [
      { key: 'mfi', title: 'MFI: ', type: 'line', styles: () => ({ color: '#2962ff' }) }
    ],
    calc: function (dataList, indicator) {
      const n = (indicator.calcParams && indicator.calcParams[0]) || 14;
      return (dataList || []).map((k, i) => {
        if (i < n) return {}; // 数据不足（与信息栏 lastMfi 的 end<=n 一致）
        let pos = 0, neg = 0;
        for (let j = i - n + 1; j <= i; j++) {
          const tp = (dataList[j].high + dataList[j].low + dataList[j].close) / 3;
          const pt = (dataList[j - 1].high + dataList[j - 1].low + dataList[j - 1].close) / 3;
          const mf = tp * (dataList[j].volume || 0);
          if (tp > pt) pos += mf; else if (tp < pt) neg += mf;
        }
        return { mfi: neg === 0 ? 100 : 100 - 100 / (1 + pos / neg) };
      });
    }
  });

  /* ---------------- 自定义画线工具（KLineChart Pro 风格扩展） ----------------
   * 几何图形：圆形 / 矩形 / 三角形 / 平行四边形
   * 斐波那契扩展：圆环 / 螺旋 / 速度阻力扇 / 趋势扩展
   * 技术分析：江恩箱 / XABCD / ABCD / 三浪 / 五浪 / 八浪 / 任意浪
   * 全部基于 registerOverlay 注册（figure type: line/rect/circle/polygon/arc/text），
   * 样式自动合并全局 overlay 样式（颜色/线宽/线型由样式设置区控制）。 */
  function registerExtraOverlays() {
    const reg = (o) => klinecharts.registerOverlay(o);

    /* 圆形：圆心 + 半径点 */
    reg({
      name: 'circle', totalStep: 3,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates }) => {
        if (coordinates.length < 2) return [];
        const c = coordinates[0], p = coordinates[1];
        return [{ type: 'circle', attrs: { center: c, radius: Math.hypot(p.x - c.x, p.y - c.y) } }];
      }
    });

    /* 矩形：对角两点 */
    reg({
      name: 'rect', totalStep: 3,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates }) => {
        if (coordinates.length < 2) return [];
        const a = coordinates[0], b = coordinates[1];
        return [{
          type: 'rect',
          attrs: { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) }
        }];
      }
    });

    /* 三角形：三点 */
    reg({
      name: 'triangle', totalStep: 4,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates }) => {
        if (coordinates.length < 3) return [];
        return [{ type: 'polygon', attrs: { coordinates: coordinates.slice(0, 3) } }];
      }
    });

    /* 平行四边形：三点，第 4 点自动补齐（D = A + C - B） */
    reg({
      name: 'parallelogram', totalStep: 4,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates }) => {
        if (coordinates.length < 3) return [];
        const [a, b, c] = coordinates;
        return [{
          type: 'polygon',
          attrs: { coordinates: [a, b, c, { x: a.x + c.x - b.x, y: a.y + c.y - b.y }] }
        }];
      }
    });

    /* 斐波那契圆环：两点区间，按 0.236/0.382/0.5/0.618/0.786/1 比例画同心虚线圆 */
    reg({
      name: 'fibonacciCircle', totalStep: 3,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates }) => {
        if (coordinates.length < 2) return [];
        const a = coordinates[0], b = coordinates[1];
        const R = Math.hypot(b.x - a.x, b.y - a.y);
        return [0.236, 0.382, 0.5, 0.618, 0.786, 1].map(r => ({
          type: 'circle', attrs: { center: a, radius: R * r },
          styles: { style: 'dashed', dashedValue: [4, 3] }
        }));
      }
    });

    /* 斐波那契螺旋：黄金比例圆弧序列（4 段 90° 弧，半径按 φ 递增） */
    reg({
      name: 'fibonacciSpiral', totalStep: 3,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates }) => {
        if (coordinates.length < 2) return [];
        const a = coordinates[0], b = coordinates[1];
        const r0 = Math.max(4, Math.hypot(b.x - a.x, b.y - a.y) / 5);
        const PI = Math.PI, PHI = 1.618;
        const figs = [];
        let cx = a.x, cy = a.y, r = r0;
        // 4 段 90° 圆弧，依次在四个象限外扩，形成黄金螺旋
        const quads = [[1, 0], [0, -1], [-1, 0], [0, 1]];
        quads.forEach(([dx, dy], i) => {
          const start = i * PI / 2, end = (i + 1) * PI / 2;
          figs.push({ type: 'arc', attrs: { center: { x: cx, y: cy }, radius: r, startAngle: start, endAngle: end } });
          // 中心沿当前弧的结束方向移动，半径放大 φ
          const ex = cx + r * Math.cos(end), ey = cy + r * Math.sin(end);
          cx = cx + (ex - cx) / 1.0; // 下一段圆心在当前弧外端
          cy = cy + (ey - cy) / 1.0;
          r = r * PHI;
        });
        return figs;
      }
    });

    /* 速度阻力扇：从起点按 1/3 / 1/2 / 2/3 斜率延伸三条射线 */
    reg({
      name: 'speedResistanceFan', totalStep: 3,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates, bounding }) => {
        if (coordinates.length < 2) return [];
        const a = coordinates[0], b = coordinates[1];
        const dx = Math.max(1, b.x - a.x);
        const slope = (b.y - a.y) / dx;
        const edge = Math.max(bounding.width, b.x + 10);
        return [1 / 3, 1 / 2, 2 / 3].map(k => ({
          type: 'line',
          attrs: { coordinates: [a, { x: edge, y: a.y + slope * k * (edge - a.x) }] }
        }));
      }
    });

    /* 斐波那契趋势扩展：A→B 回调段 + C 起点，预测 0.618 / 1 / 1.618 扩展位 */
    reg({
      name: 'fibonacciExpansion', totalStep: 4,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates, bounding }) => {
        if (coordinates.length < 3) return [];
        const [a, b, c] = coordinates;
        const base = b.y - a.y;
        const figs = [];
        [0.618, 1, 1.618].forEach((k, i) => {
          const y = c.y - base * k;
          figs.push({
            type: 'line',
            attrs: { coordinates: [{ x: c.x, y }, { x: Math.max(bounding.width, c.x + 20), y }] },
            styles: { style: 'dashed', dashedValue: [4, 3] }
          });
          figs.push({ type: 'text', attrs: { x: c.x + 4, y: y - 4, text: String(k) } });
        });
        return figs;
      }
    });

    /* 江恩箱：两点对角矩形 + 两条对角线 + 中水平 / 中垂直线 */
    reg({
      name: 'gannBox', totalStep: 3,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates }) => {
        if (coordinates.length < 2) return [];
        const a = coordinates[0], b = coordinates[1];
        const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
        const w = Math.abs(b.x - a.x), h = Math.abs(b.y - a.y);
        return [
          { type: 'rect', attrs: { x, y, width: w, height: h } },
          { type: 'line', attrs: { coordinates: [{ x, y }, { x: x + w, y: y + h }] } },
          { type: 'line', attrs: { coordinates: [{ x: x + w, y }, { x, y: y + h }] } },
          { type: 'line', attrs: { coordinates: [{ x, y: y + h / 2 }, { x: x + w, y: y + h / 2 }] }, styles: { style: 'dashed', dashedValue: [4, 3] } },
          { type: 'line', attrs: { coordinates: [{ x: x + w / 2, y }, { x: x + w / 2, y: y + h }] }, styles: { style: 'dashed', dashedValue: [4, 3] } }
        ];
      }
    });

    /* 形态折线 + 字母标注：XABCD（5 点） / ABCD（4 点） / 三浪（4 点） / 五浪（6 点） / 八浪（9 点） / 任意浪（8 点） */
    const waveReg = (name, totalStep, labels) => reg({
      name, totalStep,
      needDefaultPointFigure: true, needDefaultXAxisFigure: true, needDefaultYAxisFigure: true,
      createPointFigures: ({ coordinates }) => {
        if (coordinates.length < 2) return [];
        const figs = [{ type: 'line', attrs: { coordinates: coordinates.slice(0, totalStep - 1) } }];
        if (labels) {
          coordinates.slice(0, totalStep - 1).forEach((p, i) => {
            figs.push({ type: 'text', attrs: { x: p.x + 4, y: p.y - 6, text: labels[i] } });
          });
        }
        return figs;
      }
    });
    waveReg('xabcd', 6, ['X', 'A', 'B', 'C', 'D']);
    waveReg('abcd', 5, ['A', 'B', 'C', 'D']);
    waveReg('threeWave', 5, ['1', '2', '3']);
    waveReg('fiveWave', 7, ['1', '2', '3', '4', '5']);
    waveReg('eightWave', 10, ['1', '2', '3', '4', '5', '6', '7', '8']);
    waveReg('anyWave', 9, null); // 任意浪：最多 8 段折线，不标注
  }
  registerExtraOverlays();

  /* ---------------- 状态 ---------------- */
  const S = {
    code: '600519.SH', name: '', freq: 'daily',
    main: { ma: true, ema: false, sar: false, boll: false, st: false, vidya: false },
    subs: ['vol'],
    chart: null, mode: 'all',
    favs: JSON.parse(localStorage.getItem('skFavs') || '[]'),
    /* 目标列表：成员只能来自自选，自选里移除即自动剔除 */
    targets: JSON.parse(localStorage.getItem('skTargets') || '[]'),
    listCache: null, quote: null, inited: false,
    bars: [], /* 当前标的全部 K 线（指标信息栏数值计算用） */
    bound: false,
    theme: 'dark',
    loadToken: 0,
    /* indCache 内容指纹（自定义指标重建防抖用） */
    indCacheKey: '',
    /* 指标参数（后端桥键名 → 值），由设置面板保存后更新；为空=用后端默认参数 */
    indParams: {},
    /* 画线磁吸模式：normal / weak_magnet / strong_magnet（createOverlay 时传入） */
    magnet: 'normal',
    /* 画线默认样式（preview 风格），经 setStyles({overlay}) 应用到图表 */
    overlayStyle: {
      line: { color: '#f23645', size: 1, style: 'solid', dashedValue: [1, 0] },
      point: { color: '#f23645', borderColor: '#f23645', activeColor: '#f23645', activeBorderColor: '#f23645' },
      text: { color: '#f23645' }
    },
    /* 个股信息中心：当前视图(kline/intraday/info) + 模块数据缓存 {code: {module: {source,data}}} */
    view: 'kline',
    infoCache: {},
    /* 分时图：当前查询日期（null=最新交易日）、图表实例、最近一次数据 */
    intraDate: null,
    /* K 线截止日期（''=最新；YYYY-MM-DD=只显示到该交易日为止的 K 线与指标） */
    endDate: '',
    /* 搜索竞态令牌：慢速补涨跌幅的响应只在仍是最后一次搜索时应用 */
    searchToken: 0,
    /* 交易日历（左右箭头按真实交易日步进，自动跳过周末与节假日） */
    tradeDates: null,
    tradeDatesCode: '',
    tradeDatesPromise: null,
    tradeDatesPendingCode: '',
    intraChart: null,
    /* 模拟交易：面板开关、下单方向/类型、轮询定时器、最近一次账本快照 */
    sim: { on: false, side: 'buy', otype: 'limit', busy: false, timer: null,
           cfg: null, acc: null, positions: [], orders: [], trades: [],
           acct: 'backtest', acctFixed: false, ptrades: [] },
    intraMode: 'line',
    intraData: null,
    /* 最近一次报价（切回 K 线时用它的口径还原底部状态栏） */
    lastQuote: null
  };

  /* 指标名映射：checkbox data-i → KLineChart indicator name */
  const MAIN_IND = { ma: 'MA', ema: 'EMA', sar: 'SAR', boll: 'BOLL', st: 'SuperTrend', vidya: 'VolumeDynamicVIDYA', uma: 'UMA' };
  const SUB_IND = { vol: 'VOL', macd: 'EnhancedMACD', macd0: 'MACD', kdj: 'X_KDJ', rsi: 'X_RSI', wr: 'WR', mfi: 'MFI', obv: 'X_OBV', cci: 'CCI', dmi: 'DMI', trix: 'TRIX', boll2: 'BOLL', xtr: 'X_XTR', bb: 'BBTrendScore' };
  /* 前端 calcParams ↔ 后端桥参数键名（顺序一一对应，与 bt_indicator_bridge.py 参数键名一致）；
   * 设置面板保存时按此映射转成后端键名请求 /api/stock/indicators 重算 */
  const PARAM_MAP = {
    SuperTrend: ['st_period', 'st_mult'],
    VolumeDynamicVIDYA: ['vidya_len', 'vidya_mom', 'vidya_atr', 'vidya_sma', 'vidya_band'],
    UMA: ['ma_periods'],
    X_XTR: ['xtr_period', 'xtr_ma', 'xtr_band', 'xtr_mult'],
    BBTrendScore: ['bb_period', 'bb_dev', 'roc_period', 'acc_period', 'roll_window', 'w_nbbw', 'w_roc', 'w_acc'],
    EnhancedMACD: ['macd_fast', 'macd_slow', 'macd_signal'],
    X_RSI: ['rsi_length', 'rsi_ma_length'],
    X_KDJ: ['kdj_period', 'kdj_signal'],
    X_OBV: ['obv_ma']
  };
  /* 指标默认参数（KLineChart calcParams）；bt 版与后端桥默认值一致 */
  const MAIN_PARAMS = { MA: [5, 10, 20, 60], EMA: [12, 26], SAR: [2, 2, 20], BOLL: [20, 2], SuperTrend: [10, 3], VolumeDynamicVIDYA: [10, 20, 200, 15, 2], UMA: [20, 50, 100] };
  const SUB_PARAMS = { VOL: [5, 10], EnhancedMACD: [12, 26, 9], X_KDJ: [9, 3], X_RSI: [14, 14], WR: [6, 10, 15], MFI: [14], X_OBV: [20], CCI: [14], DMI: [14, 6], TRIX: [12, 9], BOLL: [20, 2], X_XTR: [14, 9, 20, 2], BBTrendScore: [10, 0.5] };

  /* 画线工具（preview/pro.klinecharts.com 左侧列表风格）：
   * 分组：线型 / 几何图形 / 斐波那契 / 江恩箱 / 形态 / 标注；
   * 每个工具 = [Overlay名称, 中文名, SVG图标] */
  const SVG_ICON = (inner) => `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
  const DRAW_GROUPS = [
    { name: '线型', items: [
      ['straightLine', '直线', SVG_ICON('<path d="M2 14 L14 2"/>')],
      ['rayLine', '射线', SVG_ICON('<path d="M3 13 L14 2"/><circle cx="3" cy="13" r="1.6" fill="currentColor" stroke="none"/>')],
      ['segment', '线段', SVG_ICON('<path d="M3 13 L13 3"/><circle cx="3" cy="13" r="1.6" fill="currentColor" stroke="none"/><circle cx="13" cy="3" r="1.6" fill="currentColor" stroke="none"/>')],
      ['horizontalStraightLine', '水平直线', SVG_ICON('<path d="M2 8 H14"/>')],
      ['verticalStraightLine', '垂直直线', SVG_ICON('<path d="M8 2 V14"/>')],
      ['horizontalRayLine', '水平射线', SVG_ICON('<path d="M3 8 H14"/><circle cx="3" cy="8" r="1.6" fill="currentColor" stroke="none"/>')],
      ['verticalRayLine', '垂直射线', SVG_ICON('<path d="M8 3 V14"/><circle cx="8" cy="3" r="1.6" fill="currentColor" stroke="none"/>')],
      ['horizontalSegment', '水平线段', SVG_ICON('<path d="M3 8 H13"/><circle cx="3" cy="8" r="1.6" fill="currentColor" stroke="none"/><circle cx="13" cy="8" r="1.6" fill="currentColor" stroke="none"/>')],
      ['verticalSegment', '垂直线段', SVG_ICON('<path d="M8 3 V13"/><circle cx="8" cy="3" r="1.6" fill="currentColor" stroke="none"/><circle cx="8" cy="13" r="1.6" fill="currentColor" stroke="none"/>')],
      ['priceLine', '价格线', SVG_ICON('<path d="M2 8 H14" stroke-dasharray="3 2"/>')],
      ['parallelStraightLine', '平行线', SVG_ICON('<path d="M2 5 H14 M2 11 H14"/>')],
      ['priceChannelLine', '价格通道', SVG_ICON('<path d="M2 4 H14 M2 12 H14 M2 8 H14" stroke-dasharray="3 2"/>')],
    ]},
    { name: '几何图形', items: [
      ['circle', '圆形', SVG_ICON('<circle cx="8" cy="8" r="6"/>')],
      ['rect', '矩形', SVG_ICON('<rect x="2.5" y="3" width="11" height="10"/>')],
      ['triangle', '三角形', SVG_ICON('<path d="M8 2.5 L14 13.5 L2 13.5 Z"/>')],
      ['parallelogram', '平行四边形', SVG_ICON('<path d="M4 2.5 L14 2.5 L12 13.5 L2 13.5 Z"/>')],
    ]},
    { name: '斐波那契', items: [
      ['fibonacciLine', '回调直线', SVG_ICON('<path d="M2 12 L6 4 L10 12 L14 4"/>')],
      ['fibonacciSegment', '回调线段', SVG_ICON('<path d="M2 12 L6 4 L10 12 L14 4"/><circle cx="2" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="14" cy="4" r="1.6" fill="currentColor" stroke="none"/>')],
      ['fibonacciCircle', '圆环', SVG_ICON('<circle cx="8" cy="8" r="3.5"/><circle cx="8" cy="8" r="6"/>')],
      ['fibonacciSpiral', '螺旋', SVG_ICON('<path d="M8 8 m0 -6 a6 6 0 0 1 6 6 a7 7 0 0 1 -7 7 a8 8 0 0 1 -8 -8 a9 9 0 0 1 9 -9"/>')],
      ['speedResistanceFan', '速度阻力扇', SVG_ICON('<path d="M2 12 L14 4 M2 12 L14 7 M2 12 L14 10"/>')],
      ['fibonacciExpansion', '趋势扩展', SVG_ICON('<path d="M2 12 L6 4 L14 12"/><path d="M9 12 H14" stroke-dasharray="2 2"/>')],
    ]},
    { name: '江恩', items: [
      ['gannBox', '江恩箱', SVG_ICON('<rect x="2.5" y="2.5" width="11" height="11"/><path d="M2.5 2.5 L13.5 13.5 M13.5 2.5 L2.5 13.5 M2.5 8 H13.5 M8 2.5 V13.5"/>')],
    ]},
    { name: '形态', items: [
      ['xabcd', 'XABCD形态', SVG_ICON('<path d="M2 12 L5 4 L8 13 L11 6 L14 12"/>')],
      ['abcd', 'ABCD形态', SVG_ICON('<path d="M2 12 L5 4 L8 13 L14 6"/>')],
      ['threeWave', '三浪', SVG_ICON('<path d="M2 12 L5 4 L8 12 L14 5"/>')],
      ['fiveWave', '五浪', SVG_ICON('<path d="M2 12 L4 4 L7 12 L10 3 L14 12"/>')],
      ['eightWave', '八浪', SVG_ICON('<path d="M2 12 L3.5 4 L5 12 L6.5 4 L8 12 L9.5 4 L11 12 L12.5 4 L14 12"/>')],
      ['anyWave', '任意浪', SVG_ICON('<path d="M2 12 L4 5 L7 12 L10 4 L14 12"/>')],
    ]},
    { name: '标注', items: [
      ['simpleAnnotation', '文字标注', SVG_ICON('<rect x="2.5" y="4" width="11" height="8" rx="1.5"/><path d="M5 8 H11"/>')],
      ['simpleTag', '标签', SVG_ICON('<path d="M8 2 L14 8 L8 14 L2 8 Z"/>')],
      ['brush', '涂抹', SVG_ICON('<path d="M3 13 C4 12 9 10 13 3 M6 13 L10 9"/>')],
    ]},
  ];
  /* 画线默认样式（preview 风格，可再设置） */
  const STYLE_COLORS = ['#f23645', '#2ebd85', '#ffd166', '#4a9eff', '#b18cff', '#d2d5dc'];
  const STYLE_WIDTHS = [1, 2, 3, 4];

  /* ---------------- 工具函数 ---------------- */
  function $(id) { return document.getElementById(id); }
  function fmt(v) { return (v === 0 || v == null) ? '-' : Number(v).toFixed(2); }

  /* 周期 → 后端 freq */
  function periodToFreq(period) {
    if (!period) return 'daily';
    return period.type === 'week' ? 'weekly' : period.type === 'month' ? 'monthly' : 'daily';
  }
  function freqToPeriod(freq) {
    return { type: freq === 'weekly' ? 'week' : freq === 'monthly' ? 'month' : 'day', span: 1 };
  }

  /* ---------------- 数据加载（KLineChart setDataLoader） ---------------- */
  /* 时间戳 → YYYY-MM-DD（本地时区，避免 UTC 偏移跨日） */
  function ymd(ts) {
    const d = new Date(ts);
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  /* 指标序列按 K 线根数截断：后端 indicators 与 kline 等长且同序（实测 350/350），
   * 截止日把 K 线切到 N 根，指标也必须切到前 N 个，否则图线会画到截止日之后 */
  function cutIndicators(data, len) {
    if (!data || !len) return data;
    const out = {};
    Object.keys(data).forEach(k => {
      const v = data[k];
      if (Array.isArray(v)) { out[k] = v.slice(0, len); return; }
      if (v && typeof v === 'object') {
        const o = {};
        Object.keys(v).forEach(k2 => {
          const v2 = v[k2];
          o[k2] = Array.isArray(v2) ? v2.slice(0, len) : v2;
        });
        out[k] = o;
        return;
      }
      out[k] = v;
    });
    return out;
  }

  /* 日期框可选上限＝今天（不能选未来）；下限不锁死，选到更早会给出"早于可用历史"提示。
   * 注意：不能用请求回来的最后一根——截止态下后端已经切到截止日，拿它当上限会把日期锁死在
   * 当天，之后再进日历就选不了更晚的日期 */
  function setKlineDateBounds() {
    const el = $('skKlineDate');
    if (!el) return;
    el.max = ymd(Date.now());
    el.removeAttribute('min');
  }

  /* 交易日历（左右箭头步进用）：单独取一次日线全量，按代码缓存，避免拿周/月线日期当交易日 */
  function ensureTradeDates(code) {
    if (!code) return Promise.resolve(null);
    if (S.tradeDatesCode === code && S.tradeDates && S.tradeDates.length) return Promise.resolve(S.tradeDates);
    if (S.tradeDatesPromise && S.tradeDatesPendingCode === code) return S.tradeDatesPromise;
    S.tradeDatesPendingCode = code;
    S.tradeDatesPromise = fetch(`${API}/stock/kline?code=${encodeURIComponent(code)}&days=600&freq=daily`)
      .then(r => r.json()).catch(() => null)
      .then(k => {
        const arr = ((k && k.kline) || []).map(x => String(x.date).split(/[ T]/)[0]).filter(Boolean);
        S.tradeDates = arr;
        S.tradeDatesCode = code;
        S.tradeDatesPromise = null;
        return arr;
      });
    return S.tradeDatesPromise;
  }

  /* 返回 <= d 的最后一个交易日索引（YYYY-MM-DD 字典序即日期序） */
  function nearestIdx(dates, d) {
    for (let i = dates.length - 1; i >= 0; i--) { if (dates[i] <= d) return i; }
    return -1;
  }

  /* 截止日按交易日前后挪一根；挪到最后一根等价于"最新" */
  async function stepEndDate(delta) {
    const dates = await ensureTradeDates(S.code);
    if (!dates || !dates.length) return;
    const cur = S.endDate || dates[dates.length - 1];
    let idx = nearestIdx(dates, cur);
    if (idx < 0) idx = 0;
    const t = idx + delta;
    if (t < 0 || t > dates.length - 1) return; // 已在两端，不动
    S.endDate = (t === dates.length - 1) ? '' : dates[t];
    syncKlineDateUi();
    createChart();
    if (S.sim && S.sim.on) simReplayStep(dates[t]);
  }

  function syncKlineDateUi() {
    const w = $('skKlineDateWrap'), el = $('skKlineDate');
    if (w) w.classList.toggle('on', !!S.endDate);
    if (el) el.value = S.endDate || '';
  }

  /* 截止态：顶部报价区与底部状态栏都改用截止日那根 K 线，否则会和图上最后一根对不上 */
  function renderCutQuote() {
    const el = $('skQuote'), st = $('skStatus');
    const bars = S.bars || [];
    if (!bars.length) return;
    const last = bars[bars.length - 1];
    const prev = bars.length > 1 ? bars[bars.length - 2] : null;
    const pre = prev ? prev.close : last.open;
    const pct = pre ? (last.close / pre - 1) * 100 : 0;
    const cls = pct > 0 ? 'up' : pct < 0 ? 'dn' : '';
    const amtYi = (last.turnover || 0) / 1e8;
    if (el) el.innerHTML = `<span class="qn">${S.name || ''}</span>
      <span>${ymd(last.timestamp)}</span>
      <span class="qn ${cls}">${fmt(last.close)}</span>
      <span class="${cls}">${pct > 0 ? '+' : ''}${pct.toFixed(2)}%</span>
      <span class="${cls}">${amtYi ? amtYi.toFixed(2) + '亿' : '-'}</span>`;
    if (st) {
      const amp = pre ? (last.high - last.low) / pre * 100 : 0;
      st.innerHTML = `<span>开 <b>${fmt(last.open)}</b></span><span>高 <b>${fmt(last.high)}</b></span><span>低 <b>${fmt(last.low)}</b></span>
        <span>收 <b>${fmt(last.close)}</b></span><span>振幅 <b>${amp.toFixed(2)}%</b></span>
        <span>量 <b>${(last.volume / 10000).toFixed(2)}万手</b></span><span>额 <b>${amtYi.toFixed(2)}亿</b></span>
        <span style="color:#f0b90b">截至 ${ymd(last.timestamp)}</span>`;
    }
  }

  async function fetchBars(p) {
    const token = ++S.loadToken;
    const freq = periodToFreq(p.period);
    const endDate = S.endDate || '';
    // days 是"根数"语义（后端再 +100 作为取数行数）：截止态让三种周期都覆盖约 2.8 年，
    // 避免 weekly 传 600 变成 3000 根日线聚合出 650 周的情况
    const days = endDate
      ? (freq === 'daily' ? 600 : (freq === 'weekly' ? 120 : 30))
      : (freq === 'daily' ? 250 : 120);
    const code = p.symbol.ticker;
    // 截止日交给后端在聚合前切片：周/月线最后一根才是真正只到该日的部分周期
    const endQs = endDate ? `&end=${encodeURIComponent(endDate)}` : '';
    // K 线先单独取：不能等慢速的 indicators，避免图表长时间空白
    try {
      const k = await fetch(`${API}/stock/kline?code=${encodeURIComponent(code)}&days=${days}&freq=${freq}${endQs}`).then(r => r.json()).catch(() => null);
      if (token !== S.loadToken) return; // 竞态：过期响应直接丢弃
      const raw = ((k && k.kline) || []).map(x => {
        // 容错：date 可能含时间（'2026-08-08 11:00' 等），只取日期部分；
        // 用本地时区解析，避免 UTC 午夜在 Asia/Shanghai 显示成 08:00
        const d = String(x.date).split(/[ T]/)[0];
        const t = new Date(d + 'T00:00:00').getTime();
        const o = +x.open, h = +x.high, l = +x.low, c = +x.close;
        if (!isFinite(t) || !isFinite(o) || !isFinite(h) || !isFinite(l) || !isFinite(c) || h < l) return null;
        return { timestamp: t, open: o, high: h, low: l, close: c, volume: +x.vol || 0, turnover: +(x.amount || 0) * 1000 };
      }).filter(Boolean);
      if (k && k.name) S.name = k.name;
      setKlineDateBounds();
      ensureTradeDates(code); // 预取交易日历，点箭头时无需等待
      let bars = raw;
      /* 兜底：后端若未按 end 切片（最后一根越过截止日），本地再切一次；
         正常情况后端已在聚合前切好，这里不触发（周线不会出现含未来数据的整周） */
      const endTs = endDate ? new Date(endDate + 'T23:59:59').getTime() : 0;
      if (endTs && bars.length && bars[bars.length - 1].timestamp > endTs) {
        bars = bars.filter(b => b.timestamp <= endTs);
      }
      if (endDate && !bars.length) {
        S.bars = [];
        p.callback([]);
        const first = raw.length ? ymd(raw[0].timestamp) : '—';
        const st = $('skStatus');
        if (st) st.innerHTML = `<span style="color:#f0b90b">所选截止日 ${endDate} 早于该股可用历史（最早 ${first}），已无数据</span>`;
        const qt = $('skQuote'); if (qt) qt.innerHTML = '';
        return;
      }
      S.bars = bars; // 指标信息栏数值计算的数据源（截止态=截断后）
      p.callback(bars);
      // 数据渲染后补画模拟成交点位（重建图表/切股会清空 overlay，必须每次数据到达后重画）
      // 双重延迟：首绘可能早于 K 线 pane 建立，overlay 会按空坐标算错位置
      setTimeout(() => { if (token === S.loadToken) drawSimMarks(); }, 600);
      setTimeout(() => { if (token === S.loadToken) drawSimMarks(); }, 1600);
      loadQuote(code);
      // 指标数据异步补充：到达后更新缓存并刷新指标信息栏（不阻塞 K 线渲染）；
      // 带 S.indParams（后端桥键名）→ 后端按用户设置参数重算（参数名称与 bt_indicator_bridge 一致）
      const cutLen = bars.length;
      const indQs = Object.keys(S.indParams).map(k => `&${encodeURIComponent(k)}=${encodeURIComponent(S.indParams[k])}`).join('');
      fetch(`${API}/stock/indicators?code=${encodeURIComponent(code)}&days=${days}&freq=${freq}${endQs}${indQs}`).then(r => r.json()).catch(() => null)
        .then(ind => {
          if (token !== S.loadToken) return;
          indCache.data = (ind && ind.indicators) ? cutIndicators(ind.indicators, cutLen) : null;
          renderIndBar();
          refreshCustomIndicators(); // indCache 就绪后重建自定义指标图线（后端 bt 桥计算可能晚于 K 线到达）
        });
    } catch (e) {
      console.warn('[SK-KC] fetchBars 失败', e);
      p.callback([]);
    }
  }

  /* 依赖后端 indCache 的自定义指标：数据就绪后移除重建，触发 calc 重新读取最新数据。
   * 背景：K 线先渲染、indicators 异步到达；若用户先勾选 SuperTrend/VolumeDynamicVIDYA/XTR/BBTrendScore，
   * 当时 indCache 尚空 → 图线为空，且 indicators 到达后仅刷新信息栏不重绘图表。 */
  function refreshCustomIndicators() {
    const chart = S.chart;
    if (!chart || !indCache.data) return;
    const key = indCacheKey();
    if (key === S.indCacheKey) return; // 数据未变化（如接口命中缓存返回相同内容）不重复重建
    S.indCacheKey = key;
    // 主图自定义指标（叠加 candle_pane，保留当前 calcParams）
    ['SuperTrend', 'VolumeDynamicVIDYA', 'UMA'].forEach(n => {
      const on = Object.keys(MAIN_IND).some(k => MAIN_IND[k] === n && S.main[k]);
      if (!on) return;
      const cur = chart.getIndicators({ paneId: 'candle_pane', name: n })[0];
      if (!cur) return;
      const cp = (cur.calcParams && cur.calcParams.length) ? cur.calcParams : MAIN_PARAMS[n];
      chart.removeIndicator({ paneId: 'candle_pane', name: n });
      const hasMain = chart.getIndicators({ paneId: 'candle_pane' }).length > 0;
      chart.createIndicator({ name: n, paneId: 'candle_pane', calcParams: cp }, hasMain);
    });
    // 副图自定义指标（独立 pane；bt 版覆盖内置 MACD/RSI/KDJ/OBV，一并重建）
    ['X_XTR', 'BBTrendScore', 'EnhancedMACD', 'X_RSI', 'X_KDJ', 'X_OBV'].forEach(n => {
      if (!(S.subs || []).some(k => SUB_IND[k] === n)) return;
      const cur = chart.getIndicators({ name: n })[0];
      if (!cur) return;
      const cp = (cur.calcParams && cur.calcParams.length) ? cur.calcParams : SUB_PARAMS[n];
      // 移除全部同名实例再创建单个（防连点竞态产生重复 pane）
      chart.getIndicators({ name: n }).forEach(x => chart.removeIndicator({ id: x.id }));
      chart.createIndicator({ name: n, calcParams: cp }, false);
    });
    renderIndBar();
  }

  /* indCache 内容指纹：任一指标序列长度或末值变化即视为新数据；
   * 追加 S.indParams 指纹：设置面板改参数 → 后端按新参数重算 → 强制重建图线 */
  function indCacheKey() {
    const d = indCache.data;
    if (!d) return '';
    const last = (o, k) => { const a = o && o[k]; return a && a.length ? a[a.length - 1] : ''; };
    const s = d.supertrend || {}, x = d.xtr || {}, v = d.vidya || {}, b = d.bb_score || {};
    const pKeys = Object.keys(S.indParams).sort().map(k => k + '=' + S.indParams[k]).join(',');
    return ['st', s.up && s.up.length, last(s, 'up'),
            'x', x.xtr && x.xtr.length, last(x, 'xtr'),
            'v', v.vidya && v.vidya.length, last(v, 'vidya'),
            'b', b.score && b.score.length, last(b, 'score'),
            'p', pKeys].join('|');
  }

  /* 行情栏数据 */
  async function loadQuote(code) {
    try {
      const q = await fetch(`${API}/stock/quote?code=${encodeURIComponent(code)}`).then(r => r.json()).catch(() => null);
      renderQuote(q);
    } catch (e) { console.warn('[SK-KC] quote 失败', e); }
  }

  function renderQuote(q) {
    S.lastQuote = q || null;
    /* 截止态：报价区/状态栏改用截止日那根 K 线（K 线视图才生效） */
    if (S.view === 'kline' && S.endDate && S.bars && S.bars.length) { renderCutQuote(); return; }
    const el = $('skQuote');
    if (!el) return;
    if (!q || !q.date) { el.innerHTML = ''; return; }
    const c = q.pctChg || 0;
    const cls = c > 0 ? 'up' : c < 0 ? 'dn' : '';
    el.innerHTML = `<span class="qn">${q.name}</span>
      <span>${q.date}</span>
      <span class="qn ${cls}">${q.close}</span>
      <span class="${cls}">${c > 0 ? '+' : ''}${c.toFixed(2)}%</span>
      <span class="${cls}">${(c > 0 ? '+' : '') + (q.amount ? q.amount.toFixed(2) : '-')}亿</span>`;
    const st = $('skStatus');
    if (!st) return;
    /* 分时页（单日）由 applyIntraStatus 负责当日口径，这里不覆盖；叠加态无单一日期，仍按报价写 */
    if (S.view === 'intraday' && S.intraMode !== 'multi') return;
    st.innerHTML = `<span>开 <b>${fmt(q.open)}</b></span><span>高 <b>${fmt(q.high)}</b></span><span>低 <b>${fmt(q.low)}</b></span>
      <span>振幅 <b>${q.amplitude}%</b></span><span>换手 <b>${q.turnover}%</b></span><span>量比 <b>${q.volRatio}</b></span>
      <span>PE(TTM) <b>${q.pe}</b></span><span>PB <b>${q.pb}</b></span><span>总市值 <b>${q.totalMv}亿</b></span><span>流通市值 <b>${q.circMv}亿</b></span>`;
  }

  /* 分时页底部状态栏：换成当前分时日期的口径（开/高/低/振幅/换手/量比） */
  function applyIntraStatus(d) {
    const st = $('skStatus');
    if (!st || !d || !(d.data && d.data.length)) return;
    const rows = d.data;
    const dp = d.decimal || 2;
    const pre = d.preClose || rows[0].p;
    const o = (rows[0].o != null ? rows[0].o : rows[0].p);
    let hi = -Infinity, lo = Infinity, cum = 0;
    rows.forEach(r => {
      const h = (r.h != null ? r.h : r.p), l = (r.l != null ? r.l : r.p);
      if (h > hi) hi = h;
      if (l < lo) lo = l;
      cum += (r.vol || 0);
    });
    /* 累计量优先用数据源给的 cumVol（含集合竞价，比逐分钟求和更准） */
    const lastRow = rows[rows.length - 1];
    if (lastRow && lastRow.cumVol != null && lastRow.cumVol > 0) cum = lastRow.cumVol;
    const amp = pre ? (hi - lo) / pre * 100 : 0;
    /* 换手：流通股本(亿股)=流通市值(亿)/现价；成交量“手”→万股除以 100，再除以股本得百分比 */
    let turnover = null;
    const q = S.lastQuote;
    if (q && q.circMv > 0 && q.close > 0) {
      const sharesYi = q.circMv / q.close;
      if (sharesYi > 0) turnover = cum / 100 / (sharesYi * 1e4) * 100;
    }
    /* 量比：当日累计量 / 前 5 个交易日均量（日线 volume 与分时 cumVol 同为“手”） */
    let volRatio = null;
    const bars = S.bars || [];
    const dts = d.date ? new Date(d.date + 'T00:00:00').getTime() : -1;
    const prev = bars.filter(b => b.timestamp !== dts).slice(-5);
    if (prev.length >= 3 && cum > 0) {
      const avg = prev.reduce((s, b) => s + (b.volume || 0), 0) / prev.length;
      if (avg > 0) volRatio = cum / avg;
    }
    const nz = (v, fb) => ((v == null || !isFinite(v)) ? fb : v.toFixed(2));
    st.innerHTML =
      '<span>开 <b>' + o.toFixed(dp) + '</b></span>' +
      '<span>高 <b>' + hi.toFixed(dp) + '</b></span>' +
      '<span>低 <b>' + lo.toFixed(dp) + '</b></span>' +
      '<span>振幅 <b>' + amp.toFixed(2) + '%</b></span>' +
      '<span>换手 <b>' + nz(turnover, '—') + (turnover == null ? '' : '%') + '</b></span>' +
      '<span>量比 <b>' + nz(volRatio, '—') + '</b></span>' +
      '<span style="opacity:.62">' + esc(d.date || '') + ' 口径</span>';
  }

  /* 切回 K 线/信息页时，用最近一次报价把状态栏还原成报价口径 */
  function restoreQuoteStatus() {
    if (S.lastQuote) renderQuote(S.lastQuote);
  }

  /* ---------------- 指标同步（主图/副图增删） ---------------- */
  function syncIndicators() {
    const chart = S.chart;
    if (!chart) return;
    // 主图指标
    Object.keys(MAIN_IND).forEach(k => {
      const name = MAIN_IND[k];
      const on = !!S.main[k];
      const exists = chart.getIndicators({ paneId: 'candle_pane', name: name }).length > 0;
      if (on && !exists) {
        // v10 坑：对象形式 {name,paneId} 在同 pane 已有指标时会替换掉后者，
        // 须在已有主图指标时传 isStack=true 追加（叠加到 candle_pane）
        const hasMain = chart.getIndicators({ paneId: 'candle_pane' }).length > 0;
        chart.createIndicator({ name: name, paneId: 'candle_pane', calcParams: MAIN_PARAMS[name] }, hasMain);
      }
      if (!on && exists) chart.removeIndicator({ paneId: 'candle_pane', name: name });
    });
    // 副图指标：期望集合 vs 当前集合；每个指标独立 pane（preview 布局，每个 pane 左上角有自己的信息栏）
    const wanted = (S.subs || []).map(x => SUB_IND[x]).filter(Boolean);
    const existing = chart.getIndicators().filter(x => x.paneId !== 'candle_pane');
    existing.forEach(x => { if (!wanted.includes(x.name)) chart.removeIndicator({ id: x.id }); });
    const have = new Set(existing.map(x => x.name));
    wanted.forEach(n => { if (!have.has(n)) chart.createIndicator(n, false); }); // isStack=false：每指标独立 pane
    syncDropChecks();
    renderCfgPanel();
    renderIndBar(); // 指标信息栏（pane 标题行按钮）
  }

  /* 勾选状态与计数（统一指标看板：主图/副图分区） */
  function syncDropChecks() {
    document.querySelectorAll('#skIndPanel input[type=checkbox]').forEach(cb => {
      const g = (cb.closest('label') || {}).dataset.g;
      cb.checked = g === 'main' ? !!S.main[cb.dataset.i] : S.subs.includes(cb.dataset.i);
    });
    const c = $('skIndCount');
    if (c) c.textContent = '(' + Object.values(S.main).filter(Boolean).length + '/' + S.subs.length + ')';
  }

  /* 指标参数设置面板 */
  function renderCfgPanel() {
    const box = $('skCfgList');
    const chart = S.chart;
    if (!box) return;
    if (!chart) { box.innerHTML = ''; return; }
    const inds = chart.getIndicators();
    if (!inds.length) { box.innerHTML = '<div style="padding:6px 8px;font-size:11px;color:var(--text-tertiary)">当前无指标</div>'; return; }
    box.innerHTML = inds.map(x => {
      const params = (x.calcParams || []).join(',');
      return `<div class="sk-cfg-row">
        <span class="sk-cfg-name">${x.shortName || x.name}</span>
        <input class="sk-cfg-params" data-id="${x.id}" value="${params}" placeholder="参数,逗号分隔">
        <button class="sk-cfg-apply" data-id="${x.id}">应用</button>
      </div>`;
    }).join('');
    box.querySelectorAll('.sk-cfg-apply').forEach(b => b.addEventListener('click', () => {
      const id = b.dataset.id;
      const inp = box.querySelector(`input[data-id="${id}"]`);
      if (!inp) return;
      const vals = (inp.value || '').split(',').map(v => parseFloat(v.trim())).filter(v => isFinite(v));
      if (!vals.length) { renderCfgPanel(); return; }
      chart.overrideIndicator({ id: id, calcParams: vals });
      renderCfgPanel();
    }));
  }

  /* ---------------- 指标信息栏（pane 标题行 DOM 版） ----------------
   * 每个指标一行 chip：名称(参数) + 各线最新值 + [👁 显示/隐藏] [⚙ 参数设置] [× 删除]
   * 数值来源：前端按公式算最新值，后端 indCache 有的优先用缓存。 */
  function lastArr(o, key, idx) { const a = (o && o[key]) || []; if (!a.length) return null; return a[typeof idx === 'number' ? idx : a.length - 1]; }
  function lastSma(arr, n, idx) { const end = (typeof idx === 'number') ? idx + 1 : arr.length; if (!arr || end < n) return null; let s = 0; for (let i = end - n; i < end; i++) s += arr[i]; return s / n; }
  function lastStd(arr, n, idx) { const m = lastSma(arr, n, idx); if (m == null) return null; const end = (typeof idx === 'number') ? idx + 1 : arr.length; let s = 0; for (let i = end - n; i < end; i++) s += (arr[i] - m) * (arr[i] - m); return Math.sqrt(s / n); }
  function lastEma(close, n, idx) { if (!close || close.length < 1) return null; const end = (typeof idx === 'number') ? idx + 1 : close.length; if (end < n) return null; const k = 2 / (n + 1); let e = close[0]; for (let i = 1; i < end; i++) e = close[i] * k + e * (1 - k); return e; }
  function lastSar(high, low, af, maxAf, idx) {
    if (!high || high.length < 3) return null;
    const end = (typeof idx === 'number') ? idx + 1 : high.length;
    if (end < 3) return null;
    af = af || 0.02; maxAf = maxAf || 0.2;
    let trend = 1, sar = low[0], ep = high[0], step = af;
    for (let i = 1; i < end; i++) {
      const h = high[i], l = low[i];
      if (trend === 1) {
        sar += step * (ep - sar);
        if (l < sar) { trend = -1; sar = ep; ep = l; step = af; }
        else if (h > ep) { ep = h; step = Math.min(step + af, maxAf); }
      } else {
        sar += step * (ep - sar);
        if (h > sar) { trend = 1; sar = ep; ep = h; step = af; }
        else if (l < ep) { ep = l; step = Math.min(step + af, maxAf); }
      }
    }
    return sar;
  }
  function lastWr(high, low, close, n, idx) {
    if (!close || close.length < 1) return null;
    const end = (typeof idx === 'number') ? idx + 1 : close.length;
    if (end < n) return null;
    let hh = -Infinity, ll = Infinity;
    for (let i = end - n; i < end; i++) { hh = Math.max(hh, high[i]); ll = Math.min(ll, low[i]); }
    const c = close[end - 1];
    return (hh - ll) === 0 ? 0 : -100 * (hh - c) / (hh - ll);
  }
  function lastMfi(high, low, close, volume, n, idx) {
    n = n || 14;
    if (!close || close.length < 1) return null;
    const end = (typeof idx === 'number') ? idx + 1 : close.length;
    if (end <= n) return null;
    let pos = 0, neg = 0;
    for (let i = end - n; i < end; i++) {
      const tp = (high[i] + low[i] + close[i]) / 3, pt = (high[i - 1] + low[i - 1] + close[i - 1]) / 3, mf = tp * (volume[i] || 0);
      if (tp > pt) pos += mf; else if (tp < pt) neg += mf;
    }
    return neg === 0 ? 100 : 100 - 100 / (1 + pos / neg);
  }
  function lastCci(high, low, close, n, idx) {
    n = n || 14;
    if (!close || close.length < 1) return null;
    const end = (typeof idx === 'number') ? idx + 1 : close.length;
    if (end < n) return null;
    let sum = 0; const tps = [];
    for (let i = end - n; i < end; i++) { const tp = (high[i] + low[i] + close[i]) / 3; tps.push(tp); sum += tp; }
    const ma = sum / n; let md = 0; tps.forEach(tp => md += Math.abs(tp - ma)); md /= n;
    const c = tps[tps.length - 1];
    return md === 0 ? 0 : (c - ma) / (0.015 * md);
  }
  function lastDmi(high, low, close, n, idx) {
    n = n || 14;
    if (!close || close.length < 1) return null;
    const end = (typeof idx === 'number') ? idx + 1 : close.length;
    if (end <= n) return null;
    let up = 0, dn = 0, tr = 0;
    for (let i = end - n; i < end; i++) {
      const h = high[i], l = low[i], pc = close[i - 1];
      up += (h - high[i - 1]) > 0 ? (h - high[i - 1]) : 0;
      dn += (low[i - 1] - l) > 0 ? (low[i - 1] - l) : 0;
      tr += Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
    }
    return { pdi: tr === 0 ? 0 : 100 * up / tr, mdi: tr === 0 ? 0 : 100 * dn / tr };
  }
  function lastTrix(close, n, idx) {
    n = n || 12;
    if (!close || close.length < 1) return null;
    const end = (typeof idx === 'number') ? idx + 1 : close.length;
    if (end < n * 3) return null;
    const slice = close.slice(0, end);
    const emaArr = (a) => { const k = 2 / (n + 1), o = [a[0]]; for (let i = 1; i < a.length; i++) o.push(a[i] * k + o[i - 1] * (1 - k)); return o; };
    const e3 = emaArr(emaArr(emaArr(slice)));
    const last = e3[e3.length - 1], prev = e3[e3.length - 2];
    return prev === 0 ? 0 : (last - prev) / prev * 100;
  }
  /* ---- 内置指标前端计算（与 KLineChart 图上显示一致；后端缓存仅用于自定义指标） ---- */
  function lastMacd(close, p1, p2, p3, idx) {
    const end = (typeof idx === 'number') ? idx + 1 : close.length;
    const slice = close.slice(0, end);
    const emaA = (a, n) => { const k = 2 / (n + 1), o = [a[0]]; for (let i = 1; i < a.length; i++) o.push(a[i] * k + o[i - 1] * (1 - k)); return o; };
    const e1 = emaA(slice, p1), e2 = emaA(slice, p2);
    const dif = slice.map((_, i) => e1[i] - e2[i]);
    const dea = emaA(dif, p3);
    const hist = dif.map((d, i) => (d - dea[i]) * 2); // KLineChart 柱体 = 2*(DIF-DEA)
    const i = end - 1;
    return { dif: dif[i], dea: dea[i], hist: hist[i] };
  }
  function lastKdj(high, low, close, n, k, d, idx) {
    if (!close || close.length < 1) return null;
    const end = (typeof idx === 'number') ? idx + 1 : close.length;
    let kk = 50, dd = 50;
    for (let i = 0; i < end; i++) {
      const s = Math.max(0, i - n + 1);
      let hh = -Infinity, ll = Infinity;
      for (let j = s; j <= i; j++) { hh = Math.max(hh, high[j]); ll = Math.min(ll, low[j]); }
      const rsv = (hh - ll) === 0 ? 0 : (close[i] - ll) / (hh - ll) * 100;
      kk = (k - 1) / k * kk + 1 / k * rsv;
      dd = (d - 1) / d * dd + 1 / d * kk;
    }
    return { k: kk, d: dd, j: 3 * kk - 2 * dd };
  }
  function lastRsi(close, n, idx) {
    if (!close || close.length < 1) return null;
    const end = (typeof idx === 'number') ? idx + 1 : close.length;
    if (end <= n) return null;
    let gain = 0, loss = 0;
    for (let i = 1; i <= n; i++) { const ch = close[i] - close[i - 1]; if (ch > 0) gain += ch; else loss -= ch; }
    let ag = gain / n, al = loss / n;
    for (let i = n + 1; i < end; i++) {
      const ch = close[i] - close[i - 1];
      ag = (ag * (n - 1) + (ch > 0 ? ch : 0)) / n;
      al = (al * (n - 1) + (ch < 0 ? -ch : 0)) / n;
    }
    return al === 0 ? 100 : 100 - 100 / (1 + ag / al);
  }
  function lastObv(close, volume, n, idx) {
    if (!close || !close.length) return null;
    const end = (typeof idx === 'number') ? idx + 1 : close.length;
    let obv = 0; const arr = [];
    for (let i = 0; i < end; i++) {
      if (i > 0) { if (close[i] > close[i - 1]) obv += (volume[i] || 0); else if (close[i] < close[i - 1]) obv -= (volume[i] || 0); }
      arr.push(obv);
    }
    return { obv: obv, ma: lastSma(arr, n, idx) };
  }

  /* 计算当前标的各指标值（返回 { 指标名: { vals: [[标题, 值, 颜色], ...] } }）
   * idx 为十字光标所在 bar 下标；null = 无光标，取最新一根。 */
  function computeValsAt(idx) {
    const bars = S.bars || [];
    const end = (typeof idx === 'number') ? idx + 1 : bars.length;
    const last = end - 1;
    const close = bars.map(b => b.close), high = bars.map(b => b.high), low = bars.map(b => b.low), vol = bars.map(b => b.volume);
    const ind = (indCache && indCache.data) || {};
    const out = {};
    const N = (name, key) => lastArr(ind[name], key, idx);
    const params = (name) => { const inst = S.chart && S.chart.getIndicators({ paneId: 'candle_pane', name: name })[0]; return (inst && inst.calcParams) || []; };
    const subParams = (name) => { const inst = S.chart && S.chart.getIndicators().find(x => x.paneId !== 'candle_pane' && x.name === name); return (inst && inst.calcParams) || []; };
    /* 内置指标图线默认色（KLineChart 运行时样式，与图上线条一致） */
    const indSt = (S.chart && S.chart.getStyles() && S.chart.getStyles().indicator) || {};
    const L = (indSt.lines || []).map(s => s.color);        // line 系色序（MA/EMA/BOLL/DIF…）
    const B0 = (indSt.bars || [])[0] || {};                 // bar 系（VOL/内置MACD 柱）
    const C0 = ((indSt.circles || [])[0]) || {};            // circle 系（SAR 点）
    // 主图
    const maPs = params('MA'), emaPs = params('EMA'), sarPs = params('SAR'), bollPs = params('BOLL');
    if (S.main.ma) out.MA = { vals: maPs.map((p, i) => ['MA' + p, lastSma(close, p, idx), L[i % L.length]]) };
    if (S.main.ema) out.EMA = { vals: emaPs.map((p, i) => ['EMA' + p, lastEma(close, p, idx), L[i % L.length]]) };
    if (S.main.sar) out.SAR = { vals: [['SAR', lastSar(high, low, sarPs[0] / 100, sarPs[2] / 100, idx), C0.color]] };
    if (S.main.boll) {
      const m = lastSma(close, bollPs[0], idx), s = lastStd(close, bollPs[0], idx);
      out.BOLL = { vals: [['上轨', m == null ? null : m + (bollPs[1] || 2) * s, L[0]], ['中轨', m, L[1]], ['下轨', m == null ? null : m - (bollPs[1] || 2) * s, L[2]]] };
    }
    /* 信息栏值颜色与图线颜色一致：vals 项 [label, value, color]，无 color 用默认 */
    if (S.main.st) {
      const s = ind.supertrend || {};
      const up = lastArr(s, 'up', idx), down = lastArr(s, 'down', idx);
      out.SuperTrend = { vals: [up != null ? ['ST', up, '#26a69a'] : ['ST', down, '#ef5350']] };
    }
    if (S.main.vidya) out.VolumeDynamicVIDYA = { vals: [['VIDYA', N('vidya', 'vidya'), '#ab47bc']] };
    if (S.main.uma) {
      const u = ind.uma || {};
      const c = ['#2196F3', '#4CAF50', '#9E9E9E'];
      out.UMA = { vals: [1, 2, 3].map(i => ['UMA' + i, (u['ma' + i] && lastArr(u['ma' + i], 'value', idx)), c[i - 1]]) };
    }
    // 副图
    const subs = S.subs || [];
    if (subs.includes('vol')) {
      const vp = subParams('VOL') || [5, 10, 20];
      const vma = p => lastSma(vol, p, idx);
      const lc = close.length ? (close[last] >= (close[last - 1] || 0) ? (B0.upColor || '#ef5350') : (B0.downColor || '#26a69a')) : '#787b86';
      out.VOL = { vals: vp.map((p, i) => ['MA' + p, vma(p), L[i % L.length]]).concat([['VOLUME', vol.length ? vol[last] : null, lc]]) };
    }
    /* 内置 MACD（KLineChart 官方算法，菜单保留与 EnhancedMACD 并存） */
    if (subs.includes('macd0')) {
      const inds = S.chart && S.chart.getIndicators().find(x => x.paneId !== 'candle_pane' && x.name === 'MACD');
      const mp = (inds && inds.calcParams) || [12, 26, 9];
      const m = lastMacd(close, mp[0], mp[1], mp[2], idx);
      out.MACD = { vals: [['DIF', m && m.dif, L[0] || '#f23645'], ['DEA', m && m.dea, L[1] || '#f0b90b'], ['HIST', m && m.hist, (m && m.hist > 0 ? (B0.upColor || '#ef5350') : (B0.downColor || '#26a69a'))]] };
    }
    if (subs.includes('macd')) { const m = ind.macd || {}; const h = lastArr(m, 'hist', idx); const hc = lastArr(m, 'colors', idx); const sig = lastArr(m, 'signal', idx); out.EnhancedMACD = { vals: [['DIF', lastArr(m, 'dif', idx), '#ffa726'], ['DEA', lastArr(m, 'dea', idx), '#42a5f5'], ['HIST', h, hc || '#787b86'], ['SIG', sig, sig > 0 ? '#26a69a' : sig < 0 ? '#ef5350' : '#787b86']] }; }
    if (subs.includes('kdj')) { const k = ind.kdj || {}; out.X_KDJ = { vals: [['K', lastArr(k, 'K', idx), '#2962ff'], ['D', lastArr(k, 'D', idx), '#f0b90b'], ['J', lastArr(k, 'J', idx), '#787b86']] }; }
    if (subs.includes('rsi')) { const r = ind.rsi || {}; out.X_RSI = { vals: [['RSI', lastArr(r, 'rsi', idx), '#ab47bc'], ['RSI_MA', lastArr(r, 'rsi_ma', idx), '#f0b90b']] }; }
    if (subs.includes('wr')) out.WR = { vals: [['WR', lastWr(high, low, close, (subParams('WR')[0]) || 14, idx), L[0]]] };
    if (subs.includes('mfi')) out.MFI = { vals: [['MFI', lastMfi(high, low, close, vol, (subParams('MFI')[0]) || 14, idx), '#2962ff']] };
    if (subs.includes('obv')) { const o = ind.obv || {}; out.X_OBV = { vals: [['OBV', lastArr(o, 'obv', idx), '#2962ff'], ['OBV_MA', lastArr(o, 'obv_ma', idx), '#f0b90b']] }; }
    if (subs.includes('cci')) out.CCI = { vals: [['CCI', lastCci(high, low, close, (subParams('CCI')[0]) || 14, idx), L[0]]] };
    if (subs.includes('dmi')) { const d = lastDmi(high, low, close, (subParams('DMI')[0]) || 14, idx); out.DMI = { vals: d ? [['+DI', d.pdi, L[0]], ['-DI', d.mdi, L[1]]] : [['+DI', null, L[0]], ['-DI', null, L[1]]] }; }
    if (subs.includes('trix')) out.TRIX = { vals: [['TRIX', lastTrix(close, (subParams('TRIX')[0]) || 12, idx), L[0]]] };
    if (subs.includes('boll2')) {
      const bp = subParams('BOLL') || [20, 2], m = lastSma(close, bp[0], idx), s = lastStd(close, bp[0], idx);
      out.BOLL2 = { vals: [['上轨', m == null ? null : m + (bp[1] || 2) * s, L[0]], ['中轨', m, L[1]], ['下轨', m == null ? null : m - (bp[1] || 2) * s, L[2]]] };
    }
    if (subs.includes('xtr')) { const x = ind.xtr || {}; out.X_XTR = { vals: [['X_XTR', lastArr(x, 'xtr', idx), '#2962ff'], ['X_XTR_MA', lastArr(x, 'xtr_ma', idx), '#f0b90b']] }; }
    if (subs.includes('bb')) out.BBTrendScore = { vals: [['BB', N('bb_score', 'score'), '#2962ff']] };
    return out;
  }

  /* 数值格式化：量类用 K/万，价格与指标类直接两位小数 */
  function fmtVal(v, vol) {
    if (v == null || !isFinite(v)) return '-';
    if (vol) {
      const a = Math.abs(v);
      if (a >= 1e8) return (v / 1e8).toFixed(2) + '亿';
      if (a >= 1e4) return (v / 1e4).toFixed(2) + '万';
      if (a >= 1000) return (v / 1000).toFixed(2) + 'K';
    }
    return v.toFixed(2);
  }

  /* 渲染指标信息栏：每个 pane 左上角一行（preview 布局）。
   * 主图指标 → candle_pane 左上角；副图每指标独立 pane → 各自 pane 左上角。
   * 用 chart.getDom(paneId,'main') 定位到 pane 主区域，绝对定位覆盖在标题位置。 */
  function chipValsHtml(v, isVol) {
    return (v.vals || []).map(([t, val, color]) => {
      const c = color ? ` style="color:${color}"` : '';
      return `<span${c}>${t}:<b>${fmtVal(val, isVol)}</b></span>`;
    }).join('');
  }
  function renderIndBar() {
    const chart = S.chart;
    if (!chart) return;
    // 先清掉旧的信息栏（KLineChart 重建/重绘时 DOM 子节点保留，需手动去重）
    document.querySelectorAll('.sk-pane-bar').forEach(el => el.remove());
    const vals = computeValsAt(curIdx);
    const inds = chart.getIndicators().filter(x => vals[x.name]);
    // 按 paneId 分组（一个 pane 内可能叠多个主图指标）
    const byPane = {};
    inds.forEach(inst => { (byPane[inst.paneId] = byPane[inst.paneId] || []).push(inst); });
    Object.keys(byPane).forEach(paneId => {
      const dom = chart.getDom(paneId, 'main');
      if (!dom) return;
      const bar = document.createElement('div');
      bar.className = 'sk-pane-bar';
      const kChip = (paneId === 'candle_pane')
        ? `<span class="sk-ichip sk-kchip" title="K线"><span class="ic-name">K线</span><span class="ic-vals">${kBarTxt(curIdx)}</span></span>`
        : '';
      bar.innerHTML = kChip + byPane[paneId].map(inst => {
        const v = vals[inst.name];
        const isVol = inst.name === 'VOL';
        // vals 项 [label, value, color]：label 与数值同色（继承 span 色）
        return `<span class="sk-ichip${inst.visible ? '' : ' hidden'}" title="${inst.name}">
          <span class="ic-name">${inst.shortName || inst.name}(${(inst.calcParams || []).join(',')})</span>
          <span class="ic-vals">${chipValsHtml(v, isVol)}</span>
          <span class="ic-btns">
            <button class="ic-eye${inst.visible ? '' : ' off'}" data-id="${inst.id}" title="显示/隐藏">👁</button>
            <button class="ic-set" data-pane="${inst.paneId}" data-name="${inst.name}" data-id="${inst.id}" title="参数设置">⚙</button>
            <button class="ic-del" data-pane="${inst.paneId}" data-name="${inst.name}" title="删除指标">×</button>
          </span>
        </span>`;
      }).join('');
      dom.appendChild(bar);
      /* 按钮事件：与 pane 无关，统一绑定在 bar 上 */
      bar.querySelectorAll('.ic-eye').forEach(b => b.addEventListener('click', () => {
        const inst = chart.getIndicators({ id: b.dataset.id })[0];
        if (!inst) return;
        chart.overrideIndicator({ id: inst.id, visible: !inst.visible }); // 眼睛：显示/隐藏该指标
        renderIndBar();
      }));
      bar.querySelectorAll('.ic-set').forEach(b => b.addEventListener('click', () => openIndModal(b.dataset.pane, b.dataset.name, b.dataset.id)));
      bar.querySelectorAll('.ic-del').forEach(b => b.addEventListener('click', () => {
        // ×：删除指标（主图删线，副图删整个 pane），并同步勾选状态
        if (b.dataset.pane === 'candle_pane') {
          const key = Object.keys(MAIN_IND).find(k => MAIN_IND[k] === b.dataset.name);
          if (key) S.main[key] = false;
          chart.removeIndicator({ paneId: 'candle_pane', name: b.dataset.name });
        } else {
          const key = Object.keys(SUB_IND).find(k => SUB_IND[k] === b.dataset.name);
          if (key) S.subs = S.subs.filter(x => x !== key);
          chart.removeIndicator({ paneId: b.dataset.pane });
        }
        syncIndicators();
        renderIndBar();
      }));
    });
  }

  /* ---- 十字光标联动：信息栏数值与 K 线数据随鼠标位置变化 ---- */
  let curIdx = null; // 当前光标所在 bar 下标（null=无光标，显示最新一根）
  function fmtDate(t) { const d = new Date(t), p = x => (x < 10 ? '0' : '') + x; return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()); }
  /* K 线数据信息：时间/开/高/低/收/涨跌幅/成交量（光标处；无光标=最新） */
  function kBarTxt(idx) {
    const bars = S.bars || [];
    const i = (typeof idx === 'number') ? idx : bars.length - 1;
    const b = bars[i];
    if (!b) return '';
    const prev = bars[i - 1];
    const pct = prev && prev.close ? (b.close - prev.close) / prev.close * 100 : 0;
    const up = b.close >= (prev ? prev.close : b.open);
    const c = up ? '#ef5350' : '#26a69a';
    return `<span>${b.timestamp ? fmtDate(b.timestamp) : '-'}</span> 开:${fmtVal(b.open)} 高:${fmtVal(b.high)} 低:${fmtVal(b.low)} 收:<b>${fmtVal(b.close)}</b> 涨:<b style="color:${c}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</b> 量:${fmtVal(b.volume, true)}`;
  }
  /* 光标移动时增量更新信息栏数值（只改 .ic-vals，不重建 DOM 避免闪烁） */
  function updateIndBar(idx) {
    curIdx = (typeof idx === 'number') ? idx : null;
    const chart = S.chart;
    if (!chart) return;
    const vals = computeValsAt(curIdx);
    document.querySelectorAll('.sk-pane-bar .sk-ichip').forEach(chip => {
      const vEl = chip.querySelector('.ic-vals');
      if (!vEl) return;
      if (chip.classList.contains('sk-kchip')) { vEl.innerHTML = kBarTxt(curIdx); return; }
      const v = vals[chip.title];
      if (v) vEl.innerHTML = chipValsHtml(v, chip.title === 'VOL');
    });
  }

  /* 指标设置弹窗（单指标参数编辑，参考图 EMA 设置窗口样式）。
   * 参数行 label 用 PARAM_MAP 后端桥键名（前后端参数名称一致）；
   * 保存后：① 更新图表 calcParams（信息栏/显示）② 转成后端键名写入 S.indParams
   * ③ 重新请求 /api/stock/indicators（带参数）→ 后端按新参数重算 → indCache 更新 → 图线重建 */
  function openIndModal(paneId, name, id) {
    const mask = $('skIndModal');
    const chart = S.chart;
    if (!mask || !chart) return;
    const inst = chart.getIndicators({ id: id })[0] || chart.getIndicators({ paneId: paneId, name: name })[0];
    if (!inst) return;
    const params = inst.calcParams || [];
    const short = inst.shortName || inst.name;
    const keys = PARAM_MAP[inst.name] || params.map((_, i) => 'p' + (i + 1)); // 无映射时用 p1/p2…
    $('skIndModalTitle').textContent = short + ' 参数设置';
    $('skIndModalBody').innerHTML = params.map((p, i) =>
      `<div class="sk-modal-row"><label title="${keys[i]}">${keys[i]}</label>` +
      `<input type="number" step="any" value="${p}" data-i="${i}"></div>`
    ).join('') + '<button class="sk-modal-ok" id="skIndModalOk">确定</button>';
    const ok = $('skIndModalOk');
    const confirm = () => {
      const vals = Array.from($('skIndModalBody').querySelectorAll('input')).map(i => parseFloat(i.value)).filter(v => isFinite(v));
      if (vals.length) {
        chart.overrideIndicator({ id: inst.id, calcParams: vals });
        // 仅 bt 桥指标（PARAM_MAP 有映射）联动后端重算；内置指标（MA/MACD 等）只在本地生效
        if (PARAM_MAP[inst.name] && PARAM_MAP[inst.name].length) {
          // 参数名与后端桥一致：vals[i] ↔ keys[i]（ma_periods 逗号串，后端转 tuple）
          const kv = {};
          vals.forEach((v, i) => { kv[keys[i]] = v; });
          Object.keys(kv).forEach(k => { if (kv[k] == null) delete kv[k]; });
          S.indParams = Object.assign({}, S.indParams, kv);
          S.indCacheKey = ''; // 强制重建（参数指纹已并入 indCacheKey）
          reloadIndicators();
        }
      }
      closeIndModal();
      renderIndBar();
      renderCfgPanel();
    };
    if (ok) ok.onclick = confirm;
    $('skIndModalBody').querySelectorAll('input').forEach(inp => inp.addEventListener('keydown', e => { if (e.key === 'Enter') confirm(); }));
    const closeBtn = $('skIndModalClose');
    if (closeBtn) closeBtn.onclick = closeIndModal;
    mask.onclick = e => { if (e.target === mask) closeIndModal(); };
    mask.style.display = 'flex';
  }
  /* 按当前 S.indParams 重拉指标数据（设置面板保存参数后调用） */
  async function reloadIndicators() {
    if (!S.chart || !S.code) return;
    const indQs = Object.keys(S.indParams).map(k => `&${encodeURIComponent(k)}=${encodeURIComponent(S.indParams[k])}`).join('');
    const endDate = S.endDate || '';
    const endQs = endDate ? `&end=${encodeURIComponent(endDate)}` : '';
    // 截止态按周期给根数（同 fetchBars），保证历史深度一致；参数改动后同样按截止日截断
    const daysUsed = endDate
      ? (S.freq === 'daily' ? 600 : (S.freq === 'weekly' ? 120 : 30))
      : (S.freq === 'weekly' ? 120 : 250);
    try {
      const ind = await fetch(`${API}/stock/indicators?code=${encodeURIComponent(S.code)}&days=${daysUsed}&freq=${S.freq === 'weekly' ? 'weekly' : S.freq === 'monthly' ? 'monthly' : 'daily'}${endQs}${indQs}`).then(r => r.json()).catch(() => null);
      if (!ind || !ind.indicators) return;
      indCache.data = cutIndicators(ind.indicators, (S.bars || []).length || null);
      renderIndBar();
      refreshCustomIndicators();
    } catch (e) { console.warn('[SK-KC] reloadIndicators 失败', e); }
  }
  function closeIndModal() {
    const mask = $('skIndModal');
    if (mask) mask.style.display = 'none';
  }

  /* ---------------- 画线 / 主题 / 复位 / 截图 / 全屏 ---------------- */
  /* 画线抽屉（preview 左侧列表风格）：分组工具列表 + 样式设置 + 清除 */
  function renderDrawGrid() {
    const grid = $('skDrawGrid');
    if (!grid) return;
    grid.innerHTML = DRAW_GROUPS.map(g => `
      <div class="sk-draw-group">${g.name}</div>
      ${g.items.map(it => `<button data-n="${it[0]}">${it[2]}<span>${it[1]}</span></button>`).join('')}
    `).join('');
    grid.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      if (S.chart) S.chart.createOverlay({ name: b.dataset.n, mode: S.magnet, modeSensitivity: 8 });
      closeDrops();
    }));
    // 样式设置：颜色 / 线宽 / 线型（保存到 S.overlayStyle 并应用到图表全局 overlay 默认样式）
    const colors = $('skStyleColors'), ws = $('skStyleWidth'), ls = $('skStyleLine');
    if (colors) {
      colors.innerHTML = STYLE_COLORS.map(c => `<span class="sk-c${c === S.overlayStyle.line.color ? ' on' : ''}" data-c="${c}" style="background:${c}"></span>`).join('');
      colors.querySelectorAll('.sk-c').forEach(el => el.addEventListener('click', () => {
        S.overlayStyle.line.color = el.dataset.c;
        S.overlayStyle.point.color = el.dataset.c;
        S.overlayStyle.point.activeColor = el.dataset.c;
        S.overlayStyle.text.color = el.dataset.c;
        applyOverlayStyle();
        renderDrawGrid();
      }));
    }
    if (ws) {
      ws.innerHTML = STYLE_WIDTHS.map(w => `<span class="sk-w${w === S.overlayStyle.line.size ? ' on' : ''}" data-w="${w}">${w}</span>`).join('');
      ws.querySelectorAll('.sk-w').forEach(el => el.addEventListener('click', () => {
        S.overlayStyle.line.size = parseInt(el.dataset.w, 10);
        applyOverlayStyle();
        renderDrawGrid();
      }));
    }
    if (ls) {
      ls.innerHTML = [['solid', '实线'], ['dashed', '虚线']].map(([v, t]) => `<span class="sk-l${v === S.overlayStyle.line.style ? ' on' : ''}" data-l="${v}">${t}</span>`).join('');
      ls.querySelectorAll('.sk-l').forEach(el => el.addEventListener('click', () => {
        S.overlayStyle.line.style = el.dataset.l;
        S.overlayStyle.line.dashedValue = el.dataset.l === 'dashed' ? [4, 3] : [1, 0];
        applyOverlayStyle();
        renderDrawGrid();
      }));
    }
    /* 磁吸模式（对齐 preview 的弱磁/强磁）：normal=不吸附 / weak_magnet=弱吸附 / strong_magnet=强吸附 */
    const mg = $('skMagnet');
    if (mg) {
      mg.innerHTML = [['normal', '无'], ['weak_magnet', '弱磁'], ['strong_magnet', '强磁']].map(([v, t]) => `<span class="sk-l${v === S.magnet ? ' on' : ''}" data-m="${v}">${t}</span>`).join('');
      mg.querySelectorAll('.sk-l').forEach(el => el.addEventListener('click', () => {
        S.magnet = el.dataset.m;
        renderDrawGrid();
      }));
    }
    const clear = $('skDrawClear');
    if (clear) clear.onclick = () => { if (S.chart) S.chart.removeOverlay(); };
  }

  /* 将用户画线样式应用到图表（部分合并到当前主题样式） */
  function applyOverlayStyle() {
    if (!S.chart) return;
    S.chart.setStyles({ overlay: S.overlayStyle });
  }

  /* 隐藏 KLineChart 内置 pane 标题行（主图 OHLC/指标值 + 副图指标值）：
   * 指标信息栏（skIndBar，带 眼睛/设置/× 按钮）已是唯一的信息展示排，
   * 避免与 canvas 内标题重复显示两排相同指标；hover 数值浮层一并关闭 */
  function hidePaneTitles() {
    if (!S.chart) return;
    S.chart.setStyles({
      candle: { tooltip: { showRule: 'none' } },
      indicator: { tooltip: { showRule: 'none' } }
    });
  }

  function toggleTheme() {
    S.theme = S.theme === 'dark' ? 'light' : 'dark';
    if (S.chart) { S.chart.setStyles(S.theme); hidePaneTitles(); } // 主题重设后保持 pane 标题隐藏
    document.documentElement.setAttribute('data-theme', S.theme); // 页面级 CSS 变量联动（浅色背景）
    const b = $('skThemeBtn');
    if (b) b.textContent = S.theme === 'dark' ? '深色' : '浅色';
    applyOverlayStyle(); // 主题切换会重置样式，重应用用户画线样式
  }

  function resetView() {
    if (!S.chart) return;
    S.chart.setBarSpace(8);
    S.chart.zoomAtCoordinate(1, undefined, 200);
    S.chart.scrollToRealTime(200);
  }

  function shot() {
    if (!S.chart) return;
    const url = S.chart.getConvertPictureUrl(true, 'png');
    const a = document.createElement('a');
    a.href = url;
    a.download = `${S.code}_${S.freq}_${new Date().toISOString().slice(0, 10)}.png`;
    document.body.appendChild(a); a.click(); a.remove();
  }

  function toggleFullscreen() {
    const wrap = $('page-stock-kline');
    if (!wrap) return;
    if (!document.fullscreenElement) {
      const p = wrap.requestFullscreen && wrap.requestFullscreen();
      if (p && p.then) p.then(() => S.chart && S.chart.resize()).catch(() => {});
    } else {
      document.exitFullscreen();
      setTimeout(() => S.chart && S.chart.resize(), 300);
    }
  }

  /* 关闭所有下拉 */
  function closeDrops() {
    document.querySelectorAll('.sk-drop-panel').forEach(x => x.classList.remove('show'));
    document.querySelectorAll('.sk-drop-btn').forEach(x => x.classList.remove('open'));
  }

  /* ---------------- 事件绑定 ---------------- */
  function bindEvents() {
    // 列表开关：侧栏默认收起（图表全宽），点击展开/收起并同步图表尺寸
    const lt = $('skListToggle');
    if (lt) lt.addEventListener('click', () => {
      const sb = $('skSidebar');
      if (!sb) return;
      const hidden = sb.style.display === 'none';
      sb.style.display = hidden ? 'flex' : 'none';
      setTimeout(() => { if (S.chart) S.chart.resize(); }, 60);
    });
    // 周期切换（重建图表加载该周期数据）
    $('skPeriod').querySelectorAll('.sk-p').forEach(p => p.addEventListener('click', () => {
      $('skPeriod').querySelectorAll('.sk-p').forEach(x => x.classList.remove('active'));
      p.classList.add('active');
      S.freq = p.dataset.f;
      createChart();
    }));
    // K 线截止日期：选日期后日/周/月线与全部指标只到该交易日为止；「最新」清空回到最新
    const kdEl = $('skKlineDate'), kdRs = $('skKlineDateReset');
    if (kdEl) kdEl.addEventListener('change', () => {
      const v = (kdEl.value || '').trim();
      // 选到最新交易日（或更晚）等价于"最新"：归一为空，否则状态栏会掉成截断口径（少了 PE/PB/换手）
      const dates = S.tradeDates || [];
      S.endDate = (v && dates.length && v >= dates[dates.length - 1]) ? '' : v;
      syncKlineDateUi();
      createChart();
    });
    if (kdRs) kdRs.addEventListener('click', () => {
      S.endDate = '';
      syncKlineDateUi();
      createChart();
    });
    // 左右箭头：按真实交易日前后挪一根（日/周/月线都一样按工作日步进）
    const kdPrev = $('skKlinePrev'), kdNext = $('skKlineNext');
    if (kdPrev) kdPrev.addEventListener('click', () => { stepEndDate(-1); });
    if (kdNext) kdNext.addEventListener('click', () => { stepEndDate(1); });
    syncKlineDateUi();
    // 下拉开关（指标看板统一入口；画线面板已改为图表左侧抽屉，单独处理）
    [['skIndDrop', 'skIndPanel']].forEach(([btnId, panelId]) => {
      const b = $(btnId), p = $(panelId);
      if (!b || !p) return;
      b.addEventListener('click', e => {
        e.stopPropagation();
        const open = p.classList.contains('show');
        closeDrops();
        if (!open) { p.classList.add('show'); b.classList.add('open'); }
      });
    });
    // 画线抽屉（preview 风格左侧列表）
    const dd = $('skDrawDrop'), dp = $('skDrawPanel');
    if (dd && dp) dd.addEventListener('click', e => {
      e.stopPropagation();
      const open = dp.classList.contains('show');
      closeDrops();
      if (!open) { dp.classList.add('show'); dd.classList.add('open'); }
    });
    // 画线抽屉与指标看板：面板内部点击不冒泡到 document（否则搜索框一聚焦面板就被 closeDrops 关闭）
    const ip = $('skIndPanel');
    [dp, ip].forEach(el => { if (el) el.addEventListener('click', e => e.stopPropagation()); });
    document.addEventListener('click', closeDrops);
    document.querySelectorAll('.sk-drop-panel label').forEach(lb => lb.addEventListener('click', e => { e.stopPropagation(); }));
    // 指标看板勾选（data-g 分组：main=主图 / sub=副图）
    document.querySelectorAll('#skIndPanel input[type=checkbox]').forEach(cb => cb.addEventListener('change', () => {
      const g = (cb.closest('label') || {}).dataset.g;
      if (g === 'main') {
        S.main[cb.dataset.i] = cb.checked;
      } else {
        S.subs = Array.from(document.querySelectorAll('#skIndPanel label[data-g="sub"] input[type=checkbox]')).filter(x => x.checked).map(x => x.dataset.i);
      }
      syncIndicators();
    }));
    // 指标看板搜索框：按名称过滤（搜到后可直接勾选）
    const indS = $('skIndSearch');
    if (indS) indS.addEventListener('input', () => {
      const q = indS.value.trim().toLowerCase();
      document.querySelectorAll('#skIndPanel label[data-g]').forEach(lb => {
        const match = !q || lb.textContent.toLowerCase().includes(q);
        lb.style.display = match ? '' : 'none';
      });
    });
    // 搜索
    const sb = $('skSearchBtn'), si = $('skSearch');
    if (sb) sb.addEventListener('click', doSearch);
    if (si) si.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
    // Tab（沪深A股 / 自选 / 目标）
    document.querySelectorAll('.s-tab').forEach(t => t.addEventListener('click', () => {
      switchMode(t.dataset.m);
    }));
    // 添加股票：展开面板 / 粘贴代码 / 读本地 txt·csv
    const ab = $('skAddBtn'), ap = $('skAddPanel');
    if (ab && ap) ab.addEventListener('click', () => {
      const willShow = !ap.classList.contains('show');
      ap.classList.toggle('show', willShow);
      ab.textContent = willShow ? '－ 收起' : '＋ 添加股票';
    });
    const at = $('skAddText');
    if (at) at.addEventListener('keydown', e => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addCodesFromText(); }
    });
    const ao = $('skAddOk');
    if (ao) ao.addEventListener('click', addCodesFromText);
    const af = $('skAddFile');
    if (af) af.addEventListener('change', () => {
      const f = af.files && af.files[0];
      if (!f) return;
      const rd = new FileReader();
      /* 文件可能是 UTF-8（记事本）或 GBK/GB2312（Excel 导出 CSV），按字节读后探测编码 */
      rd.onload = () => {
        let text = '';
        try {
          text = new TextDecoder('utf-8', { fatal: false }).decode(rd.result);
          if (text.indexOf('\uFFFD') >= 0) {
            try { text = new TextDecoder('gbk').decode(rd.result); } catch (e2) { /* 保持 utf-8 结果 */ }
          }
        } catch (e) { text = ''; }
        const codes = parseCodes(text);
        const added = addToFavs(codes);
        const ro = (S.mode === 'plan_bt' || S.mode === 'plan_live');
        const tip = $('skAddTip');
        if (tip) tip.textContent = codes.length
          ? `${f.name}：识别 ${codes.length} 个代码，新增 ${added} 个到自选` + (ro ? '（回测/实盘列表只读，来自 JSON）' : '')
          : `${f.name}：没识别到 6 位股票代码`;
        af.value = '';
        if (codes.length) switchMode('fav');
      };
      rd.onerror = () => { const tip = $('skAddTip'); if (tip) tip.textContent = '文件读取失败'; };
      rd.readAsArrayBuffer(f);
    });
    // K线|信息 视图切换（个股信息中心）
    const vts = $('skViewTabs');
    if (vts) vts.querySelectorAll('.sk-view-tab').forEach(t => t.addEventListener('click', () => {
      setView(t.dataset.v);
    }));
    // 分时：日期选择 / 回到最新
    const idr = $('skIntraDate');
    if (idr) idr.addEventListener('change', () => {
      const v = idr.value;
      if (v) { S.intraDate = v; loadIntraday(S.code, v); }
    });
    const ilb = $('skIntraLatest');
    if (ilb) ilb.addEventListener('click', () => { S.intraDate = null; loadIntraday(S.code, null); });
    // 分时 / 1分钟K 模式切换
    const im = $('skIntraMode');
    if (im) im.addEventListener('click', (e) => {
      const sp = e.target.closest('[data-m]');
      if (!sp) return;
      S.intraMode = sp.dataset.m;
      im.querySelectorAll('span').forEach(x => x.classList.toggle('on', x === sp));
      // 模式切换后重新取数：multi 需要额外的 5 日完整序列
      loadIntraday(S.code, S.intraDate);
    });
    // 工具按钮
    const tb = $('skThemeBtn'); if (tb) tb.addEventListener('click', toggleTheme);
    const rb = $('skResetBtn'); if (rb) rb.addEventListener('click', resetView);
    const sb2 = $('skShotBtn'); if (sb2) sb2.addEventListener('click', shot);
    const fb = $('skFsBtn'); if (fb) fb.addEventListener('click', toggleFullscreen);
    renderDrawGrid();
  }

  async function doSearch() {
    const q = $('skSearch') && $('skSearch').value.trim();
    if (!q) return;
    // 搜索时自动展开侧栏，让搜索结果可见
    const sb = $('skSidebar');
    if (sb && sb.style.display === 'none') { sb.style.display = 'flex'; setTimeout(() => { if (S.chart) S.chart.resize(); }, 60); }
    try {
      const r = await (await fetch(`${API}/stock/search?q=${encodeURIComponent(q)}`)).json();
      const list = r.list || [];
      // 先立即出结果（名称/代码来自搜索接口），再用腾讯批量补涨跌幅：
      // 否则等批量行情返回的 1~4 秒里列表还停在上一次内容，用户会点到错的股票
      renderList(list.map(x => ({ code: x.code, name: x.name, pctChg: 0 })), true);
      const t = ++S.searchToken;
      buildNameItems(list.map(x => x.code)).then(items => {
        if (t !== S.searchToken) return; // 期间又搜了别的，丢弃
        renderList(items, true);
      }).catch(() => { /* 补涨跌幅失败保留上面即时结果 */ });
    } catch (e) { console.warn('[SK-KC] search fail', e); }
  }

  /* 目标列表：成员必须是自选里的股票；不在自选的自动剔除并落盘 */
function syncTargets() {
  const favSet = {};
  (S.favs || []).forEach(c => { favSet[String(c).trim()] = 1; });
  const out = [];
  (S.targets || []).forEach(c => {
    const k = String(c).trim();
    if (k && favSet[k] && out.indexOf(k) < 0) out.push(k);
  });
  S.targets = out;
  localStorage.setItem('skTargets', JSON.stringify(out));
  return out;
}

/* 交易板块识别：沪市主板 / 深市主板 / 创业板 / 科创板 / 北交所 */
function boardOf(code) {
  const num = String(code || '').split('.')[0];
  if (/^(688|689)/.test(num)) return { k: '科', c: '#c56cf0', t: '科创板' };
  if (/^(300|301)/.test(num)) return { k: '创', c: '#f0b90b', t: '创业板' };
  if (/^(4|8|920)/.test(num)) return { k: '北', c: '#9aa4b2', t: '北交所' };
  if (/^6/.test(num)) return { k: '沪', c: '#4a9eff', t: '沪市主板' };
  if (/^(000|001|002|003)/.test(num)) return { k: '深', c: '#089981', t: '深市主板' };
  return { k: '', c: '', t: '' };
}

function txCode(code) {
  const p = String(code || '').trim().split('.');
  const num = p[0];
  let m = (p[1] || '').toUpperCase();
  if (!m) {
    if (/^6/.test(num)) m = 'SH';
    else if (/^(0|3)/.test(num)) m = 'SZ';
    else m = 'BJ';
  }
  return ({ SH: 'sh', SZ: 'sz', BJ: 'bj' })[m] ? ({ SH: 'sh', SZ: 'sz', BJ: 'bj' })[m] + num : '';
}

/* 腾讯批量行情：一次 script 注入取全部代码的现价/昨收/涨跌幅（返回 {纯数字代码: {name,price,preClose,pct}}）
 * 字段位：a[1]=名称 a[3]=现价 a[4]=昨收 a[31]=涨跌额 a[32]=涨跌幅% a[33]=最高 a[34]=最低 */
function loadTxQuotes(codes) {
  return new Promise(resolve => {
    const list = [];
    (codes || []).forEach(c => {
      const t = txCode(c);
      if (t && list.indexOf(t) < 0) list.push(t);
    });
    if (!list.length) return resolve({});
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      const out = {};
      list.forEach(tc => {
        try {
          const raw = window['v_' + tc];
          if (raw == null) return;
          const a = String(raw).split('~');
          if (a.length < 34) return;
          const price = parseFloat(a[3]);
          const pre = parseFloat(a[4]);
          const pct = parseFloat(a[32]);
          out[tc.replace(/^(sh|sz|bj)/, '')] = {
            name: a[1] || '',
            price: isFinite(price) ? price : null,
            preClose: isFinite(pre) ? pre : null,
            pct: isFinite(pct) ? pct : 0
          };
        } catch (e) { /* 单只解析失败忽略 */ }
      });
      list.forEach(tc => { try { delete window['v_' + tc]; } catch (e) { /* 忽略 */ } });
      resolve(out);
    };
    const s = document.createElement('script');
    s.charset = 'gbk';
    s.src = 'http://qt.gtimg.cn/q=' + list.join(',') + '&_=' + Date.now();
    s.onload = finish;
    s.onerror = finish;
    (document.head || document.body).appendChild(s);
    setTimeout(finish, 4000);
  });
}

/* 代码列表 → 列表项：腾讯批量给名称/涨跌幅，拿不到的再用 listCache / quote 补名 */
async function buildNameItems(codes) {
  const uniq = [];
  (codes || []).forEach(c => {
    const k = String(c).trim();
    if (k && uniq.indexOf(k) < 0) uniq.push(k);
  });
  if (!uniq.length) return [];
  const tx = await loadTxQuotes(uniq);

  if (!S.listCache) {
    try {
      const _r = await (await fetch(`${API}/stock/list?size=300`)).json();
      S.listCache = (_r.list || []).map(x => ({ code: x.code, name: x.name, pctChg: x.pctChg }));
    } catch (e) { S.listCache = []; }
  }
  const _nameMap = {};
  (S.listCache || []).forEach(x => { if (x.code) _nameMap[x.code] = x.name; });

  const _missing = [];
  uniq.forEach(k => {
    const num = k.split('.')[0];
    if (!(tx[num] && tx[num].name) && !_nameMap[k] && _missing.indexOf(k) < 0) _missing.push(k);
  });
  if (_missing.length) {
    await Promise.all(_missing.map(async k => {
      try {
        const _q = await (await fetch(`${API}/stock/quote?code=${encodeURIComponent(k)}`)).json();
        if (_q && _q.name) _nameMap[k] = _q.name;
      } catch (e) { /* 保持代码回退 */ }
    }));
  }
  return uniq.map(k => {
    const num = k.split('.')[0];
    const t = tx[num];
    return {
      code: k,
      name: (t && t.name) || _nameMap[k] || k,
      pctChg: t ? t.pct : 0,
      price: t ? t.price : null
    };
  });
}

/* 文本 → 归一化 A 股代码（6 位数字，可带 .SH/.SZ/.BJ；无后缀按首位推断） */
function parseCodes(text) {
  const out = [];
  const src = String(text || '').replace(/\uFEFF/g, '');
  const re = /(\d{6})\s*(?:[.,]\s*(SH|SZ|BJ))?/gi;
  let m;
  while ((m = re.exec(src))) {
    const num = m[1];
    if (!/^[03648]/.test(num)) continue; // A股代码首位白名单，避开日期/金额等无关 6 位数字
    let suf = (m[2] || '').toUpperCase();
    if (!suf) {
      if (/^6/.test(num)) suf = 'SH';
      else if (/^(0|3)/.test(num)) suf = 'SZ';
      else suf = 'BJ'; // 4/8 开头=北交所
    }
    const code = num + '.' + suf;
    if (out.indexOf(code) < 0) out.push(code);
  }
  return out;
}

/* 从文本域粘贴内容导入自选 */
function addCodesFromText() {
  const ta = $('skAddText');
  const tip = $('skAddTip');
  const codes = parseCodes(ta ? ta.value : '');
  if (!codes.length) {
    if (tip) tip.textContent = '没识别到 6 位股票代码（示例：600519 或 000001.SZ）';
    return;
  }
  const added = addToFavs(codes);
  const ro = (S.mode === 'plan_bt' || S.mode === 'plan_live');
  if (tip) tip.textContent = `识别 ${codes.length} 个代码，新增 ${added} 个到自选` +
    (ro ? '（回测/实盘列表只读，来自 JSON）' : '');
  if (ta) ta.value = '';
  switchMode('fav');
}

/* 批量加入自选，返回新增数量（顺带清掉历史遗留的重复项） */
function addToFavs(codes) {
  const uniq = [];
  (S.favs || []).forEach(c => { const k = String(c).trim(); if (k && uniq.indexOf(k) < 0) uniq.push(k); });
  S.favs = uniq;
  let added = 0;
  (codes || []).forEach(c => {
    const k = String(c).trim();
    if (k && !S.favs.includes(k)) { S.favs.push(k); added++; }
  });
  localStorage.setItem('skFavs', JSON.stringify(S.favs));
  syncTargets();
  return added;
}

/* 侧栏展开 + Tab 切到指定列表 + 重载列表 */
function switchMode(mode) {
  S.mode = mode;
  S.sim.trades = [];
  document.querySelectorAll('.s-tab').forEach(x => x.classList.toggle('active', x.dataset.m === mode));
  const sb = $('skSidebar');
  if (sb && sb.style.display === 'none') sb.style.display = 'flex';
  loadList();
  simRefresh();
  setTimeout(() => { if (S.chart) S.chart.resize(); }, 60);
}

async function loadList() {
  try {
    const el = $('skList');
    if (!el) return;
    if (S.mode === 'plan_bt' || S.mode === 'plan_live') {
      const md = S.mode === 'plan_bt' ? 'backtest' : 'live';
      let items = [];
      try {
        const r = await (await fetch(`${API}/sim/plan/pool?mode=${md}`)).json();
        items = (r.items || []).map(x => ({ code: x.code, name: x.name, pctChg: x.pct_chg }));
      } catch (e) { console.warn('[SK-KC] plan pool fail', e); }
      hideLinkBar();
      if (!items.length) {
        el.innerHTML = '<div class="s-empty">' + (md === 'backtest' ? '回测列表为空' : '实盘列表为空') +
          '<br>交易持仓页执行批次 JSON 后自动出现<br>本列表只读，手工添加请用「自选」</div>';
        return;
      }
      renderList(items, false);
      return;
    }
    if (S.mode === 'fav' || S.mode === 'target') {
      syncTargets();
      const codes = S.mode === 'fav' ? S.favs : S.targets;
      renderList(await buildNameItems(codes), false);
      hideLinkBar();
      return;
    }
      const link = (typeof App !== 'undefined' && App.linkState) || null;
      if (link && link.sector) {
        const p = link.sector.date ? `&date=${link.sector.date}` : '';
        try {
          const r = await (await fetch(`${API}/ch/sector-members?type=${link.sector.type}&code=${link.sector.code}${p}`)).json();
          const items = (r.members || []).map(x => ({ code: x.code, name: x.name, pctChg: x.changePct }));
          renderList(items, false);
          renderLinkBar(link.sector);
          return;
        } catch (e) { console.warn('[SK-KC] sector list fail', e); }
      }
      if (!S.listCache) {
        const r = await (await fetch(`${API}/stock/list?size=120`)).json();
        S.listCache = (r.list || []).map(x => ({ code: x.code, name: x.name, pctChg: x.pctChg }));
      }
      renderList(S.listCache, false);
      hideLinkBar();
    } catch (e) { console.warn('[SK-KC] list fail', e); }
  }

  function applyLink() {
    const link = (typeof App !== 'undefined' && App.linkState) || null;
    if (link && link.stock && link.stock.code) { loadStock(link.stock.code); return; }
    loadStock(S.code);
  }

  function renderLinkBar(sector) {
    let bar = $('skLinkBar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'skLinkBar';
      bar.style.cssText = 'display:flex;align-items:center;gap:10px;padding:6px 12px;margin-bottom:8px;background:rgba(41,98,255,0.10);border:1px solid rgba(41,98,255,0.35);border-radius:6px;font-size:12px;color:var(--text-main);flex-wrap:wrap';
      const listEl = $('skList');
      if (listEl) listEl.parentNode.insertBefore(bar, listEl);
    }
    bar.style.display = 'flex';
    const tag = sector.type === 'concept' ? '概念' : '行业';
    bar.innerHTML = `<span style="font-weight:600">当前列表：${tag} · ${sector.name}</span>` +
      `<span style="font-size:11px;color:var(--text-secondary)">${sector.date || '最新交易日'} · 点击行切换K线</span>` +
      `<span id="skLinkExit" style="cursor:pointer;color:#f23645;font-weight:600;margin-left:auto">✕ 退出板块视图</span>`;
    const exit = $('skLinkExit');
    if (exit) exit.addEventListener('click', () => {
      if (window.App) App.clearLink();
      S.listCache = null;
      loadList();
    });
  }

  function hideLinkBar() {
    const bar = $('skLinkBar');
    if (bar) bar.style.display = 'none';
  }

  function renderList(items, isSearch) {
    const el = $('skList');
    if (!el) return;
    const showTgt = (S.mode === 'fav' || S.mode === 'target');
    const rows = items.map(it => {
      const cls = it.code === S.code ? 'active' : '';
      const fav = S.favs.includes(it.code);
      const tgt = S.targets.includes(it.code);
      const pc = it.pctChg || 0;
      const color = pc > 0 ? RED : pc < 0 ? GREEN : GRAY;
      const tgtHtml = showTgt
        ? `<span class="tgt ${tgt ? 'on' : ''}" data-tgt="${it.code}" title="${tgt ? '移出目标' : '加入目标'}">${tgt ? '◎' : '○'}</span>`
        : '';
      const bd = boardOf(it.code);
      const bdHtml = bd.k
        ? `<span class="bd" title="${bd.t}" style="color:${bd.c};background:${bd.c}1f;border:1px solid ${bd.c}40">${bd.k}</span>`
        : '';
      return `<div class="s-item ${cls}" data-code="${it.code}" data-name="${it.name}">
        <span class="fav ${fav ? 'on' : ''}" data-fav="${it.code}">${fav ? '★' : '☆'}</span>
        ${tgtHtml}
        <span class="nm">${it.name}</span>
        <span class="cdw">${bdHtml}<span class="cd">${it.code.split('.')[0]}</span></span>
        <span class="pc" style="color:${color}">${pc > 0 ? '+' : ''}${pc.toFixed(2)}%</span>
      </div>`;
    }).join('');
    el.innerHTML = rows || `<div class="s-empty">${S.mode === 'target'
      ? '目标列表为空<br>到「自选」点 ○ 加入目标'
      : (S.mode === 'fav' ? '自选为空<br>点上方「＋ 添加股票」导入' : '暂无数据')}</div>`;
    el.querySelectorAll('.s-item').forEach(row => row.addEventListener('click', e => {
      // 目标标记：只能在自选里操作（目标成员强制来自自选）
      if (e.target.classList.contains('tgt')) {
        e.stopPropagation();
        const c = e.target.dataset.tgt;
        if (!S.favs.includes(c)) {
          S.favs.push(c);
          localStorage.setItem('skFavs', JSON.stringify(S.favs));
        }
        const ti = S.targets.indexOf(c);
        if (ti >= 0) S.targets.splice(ti, 1); else S.targets.push(c);
        syncTargets();
        loadList();
        return;
      }
      if (e.target.classList.contains('fav')) {
        if (window.App && App.linkState && App.linkState.sector) {
          App.saveLink({ sector: App.linkState.sector, stock: { code: row.dataset.code, name: row.dataset.name } });
        }
        e.stopPropagation();
        const c = e.target.dataset.fav;
        const idx = S.favs.indexOf(c);
        if (idx >= 0) S.favs.splice(idx, 1); else if (!S.favs.includes(c)) S.favs.push(c);
        localStorage.setItem('skFavs', JSON.stringify(S.favs));
        syncTargets(); // 移出自选时自动从目标剔除
        if (S.mode === 'fav' || S.mode === 'target') loadList(); else renderList(items, isSearch);
        return;
      }
      loadStock(row.dataset.code);
    }));
  }

  /* ---------------- 股票切换 / 初始化 ---------------- */
  function loadStock(code) {
    S.code = code;
    S.bars = []; // 切股先清空旧标的行情，避免成交点位用到上一只的坐标系
    simSyncCode();
    document.querySelectorAll('.s-item').forEach(x => x.classList.toggle('active', x.dataset.code === code));
    if (!S.chart) return;
    createChart(); // 重建图表加载新标的（画线一并清空，切换标的场景合理）
    // 信息中心联动：换股后若当前在信息/分时 tab，立即刷新对应内容（缓存按 code 隔离，旧股数据保留）
    if (S.view === 'info') loadStockInfo(code);
    else if (S.view === 'intraday') { S.intraDate = null; loadIntraday(code, null); }
  }

  /* ---------------- 初始化 / 重建图表 ---------------- */
  /* 创建（或重建）KLineChart 实例：
   * 重建顺序 init → setSymbol → setPeriod → setDataLoader，
   * 该顺序保证 setDataLoader 挂载后立即以当前上下文触发 getBars 加载数据。
   * 周期/股票切换均走重建，可靠性优先（重建会清空画线，可接受）。 */
  function createChart() {
    const el = $('stockChart');
    if (!el) return null;
    if (S.chart) { try { klinecharts.dispose(S.chart); } catch (e) { /* 忽略 */ } S.chart = null; }
    el.style.height = '';
    const chart = klinecharts.init(el, { locale: 'zh-CN', timezone: 'Asia/Shanghai', styles: S.theme });
    chart.setOffsetRightDistance(12);
    chart.setSymbol({ ticker: S.code, pricePrecision: 2 });
    chart.setPeriod(freqToPeriod(S.freq));
    chart.setDataLoader({ getBars: fetchBars });
    S.chart = chart;
    // 十字光标联动：信息栏数值与 K 线数据随鼠标移动变化（移出图表恢复最新值）
    // v10 的 onCrosshairChange 回调只带 {x, y, paneId}（图表内像素坐标），
    // 需用 convertFromPixel 反查光标所在 bar 的下标。
    chart.subscribeAction('onCrosshairChange', (data) => {
      let di = null;
      if (data && typeof data.x === 'number' && typeof chart.convertFromPixel === 'function') {
        try {
          const pt = chart.convertFromPixel({ x: data.x, y: data.y });
          di = (pt && typeof pt.dataIndex === 'number') ? pt.dataIndex : null;
          if (di != null && (di < 0 || di >= (S.bars || []).length)) di = null;
        } catch (e) { di = null; }
      }
      updateIndBar(di);
    });
    // 兜底：鼠标离开图表容器恢复最新值（个别环境 mouseleave 被 KLineChart 忽略）
    el.addEventListener('mouseleave', () => updateIndBar(null));
    syncIndicators();
    hidePaneTitles(); // 隐藏 canvas 内置标题行，指标信息栏（带按钮）为唯一信息排
    /* DOM 成交点位层不随 K 线滚动/缩放自动移动，必须在交互期间按新坐标重画：
       拖动（pointerdown+move）、滚轮缩放、内置缩放按钮（click）、双击复位 */
    if (!S.markBound) {
      S.markBound = true;
      let drag = false, raf = 0;
      const later = () => {
        if (raf) return;
        raf = requestAnimationFrame(() => { raf = 0; drawSimMarks(); });
      };
      el.addEventListener('pointerdown', () => { drag = true; later(); }, true);
      window.addEventListener('pointerup', () => { drag = false; }, true);
      /* 跟手：拖出图表边界（快速甩动）也要继续重画，所以 pointermove 挂在 window */
      window.addEventListener('pointermove', () => { if (drag) later(); }, { passive: true });
      el.addEventListener('wheel', later, { passive: true });
      el.addEventListener('touchmove', later, { passive: true });
      el.addEventListener('touchstart', later, { passive: true });
      el.addEventListener('click', later, true);
      el.addEventListener('dblclick', later, true);
    }
    return chart;
  }

  function init() {
    console.log('[SK-KC] init route=' + (window.App ? window.App.state.currentRoute : '?'));
    const el = $('stockChart');
    if (!el) return;
    if (S.inited) { loadList(); applyLink(); return; }
    S.inited = true;
    if (!S.bound) { bindEvents(); S.bound = true; }
    createChart();
    loadList();
    applyLink();
    window.addEventListener('resize', () => { if (S.chart) S.chart.resize(); });
    /* 容器尺寸变化（侧栏展开/收起、列表切换）不会触发 window.resize，
       但 klinecharts 必须 resize 后才会重算坐标，否则 overlay/价格标签会画到旧位置上 */
    try {
      if (window.ResizeObserver) {
        let roT = null;
        new ResizeObserver(() => {
          clearTimeout(roT);
          roT = setTimeout(() => { if (S.chart) { S.chart.resize(); drawSimMarks(); } }, 120);
        }).observe(el);
      }
    } catch (e) { /* 忽略 */ }
    initSim();
    simRefresh();
  }

  /* 覆盖挂载：即使原实现已被 App.init 提前触发过，也强制切换为新实现 */
  function ensureKcActive() {
    const el = $('stockChart');
    if (!el) return;
    if (window.echarts) {
      try {
        const old = echarts.getInstanceByDom(el);
        if (old) old.dispose();
      } catch (e) { /* 忽略旧实例清理异常 */ }
    }
    if (!S.inited) init();
  }

  /* ================= 个股信息中心（K线|信息 tab）：板块地位·趋势核心 + 10 信息模块 ================= */
  const INFO_MODULES = ['sector', 'mfcurve', 'bench', 'moneyflow', 'dragon', 'finance', 'holder', 'news', 'valuation', 'share', 'hot'];
  /* CH 快模块优先渲染；akshare 慢模块（news/share）后台补位，单模块最多等 20s */
  const INFO_FAST = ['sector', 'mfcurve', 'bench', 'moneyflow', 'dragon', 'finance', 'holder', 'valuation', 'hot'];
  const INFO_SLOW = ['news', 'share'];
  const INFO_TITLES = {
    sector: '板块地位·趋势', mfcurve: '个股主力资金净流入曲线', bench: '行情对标', moneyflow: '资金流透视', dragon: '龙虎榜·异动',
    finance: '财务快照', holder: '股东·质押', news: '公告·舆情', valuation: '估值分位',
    share: '解禁·分红', hot: '热度·地位'
  };
  let infoCharts = [];

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function disposeInfoCharts() { infoCharts.forEach(c => { try { c.dispose(); } catch (e) { /* 忽略 */ } }); infoCharts = []; }

  /* 视图切换：K线 ↔ 分时 ↔ 信息 */
  function setView(v) {
    S.view = v;
    /* 截止日期框只属于 K 线页（分时页有自己的日期框） */
    const kdw = $('skKlineDateWrap');
    if (kdw) kdw.style.display = v === 'kline' ? '' : 'none';
    const scroll = document.querySelector('.sk-chart-scroll');
    const info = $('skInfoPanel');
    const intra = $('skIntradayPanel');
    if (scroll) scroll.style.display = v === 'kline' ? '' : 'none';
    if (info) info.style.display = v === 'info' ? 'grid' : 'none';
    if (intra) intra.style.display = v === 'intraday' ? 'flex' : 'none';
    document.querySelectorAll('.sk-view-tab').forEach(x => x.classList.toggle('active', x.dataset.v === v));
    if (v === 'info') {
      disposeIntraday();
      restoreQuoteStatus();
      loadStockInfo(S.code);
    } else if (v === 'intraday') {
      disposeInfoCharts();
      loadIntraday(S.code, S.intraDate);
    } else {
      disposeInfoCharts();
      disposeIntraday();
      restoreQuoteStatus();
    }
    setTimeout(() => { if (S.chart) S.chart.resize(); }, 80);
  }

  function disposeIntraday() {
    if (S.intraChart) { try { S.intraChart.dispose(); } catch (e) { /* 忽略 */ } S.intraChart = null; }
  }

  /* 分时：拉取指定日期（缺省=最新交易日）逐分钟数据并绘制 */
  async function loadIntraday(code, date) {
    const box = $('skIntraChart');
    if (!box) return;
    if (!S.intraChart) box.innerHTML = '<div class="sk-empty" style="padding:40px;text-align:center">正在加载分时数据…</div>';
    const isMulti = S.intraMode === 'multi';
    let d = null;
    try {
      let u = `${API}/stock/intraday?code=${encodeURIComponent(code)}` +
              (date && !isMulti ? `&date=${encodeURIComponent(date)}` : '');
      if (isMulti) u += '&multi=1';
      const r = await fetch(u);
      d = r.ok ? await r.json() : null;
    } catch (e) { d = null; }
    if (S.code !== code || S.view !== 'intraday') return; // 竞态：已换股/切走则丢弃
    const vb = $('skIntraVolBar');
    if (!d || d.error) {
      disposeIntraday();
      if (vb) vb.innerHTML = '';
      box.innerHTML = '<div class="sk-empty" style="padding:40px;text-align:center">分时数据获取失败：' + esc((d && d.error) || '网络异常') + '</div>';
      return;
    }
    if (!(d.data && d.data.length)) {
      disposeIntraday();
      if (vb) vb.innerHTML = '';
      box.innerHTML = '<div class="sk-empty" style="padding:40px;text-align:center">该标的暂无可用的分时数据（非交易日或数据源缺失）</div>';
      return;
    }
    const di = $('skIntraDate');
    if (di) {
      di.disabled = isMulti;
      if (!isMulti) {
        if (d.dates && d.dates.length) { di.min = d.dates[0]; di.max = d.dates[d.dates.length - 1]; }
        di.value = d.date || '';
      }
    }
    const hint = $('skIntraHint');
    if (hint && d.dates && d.dates.length) {
      hint.textContent = isMulti
        ? '5 个交易日重叠对比，各日以自身昨收归一为 100'
        : '可选 ' + d.dates[0] + ' ~ ' + d.dates[d.dates.length - 1] + '（共 ' + d.dates.length + ' 个交易日）';
    }
    if (isMulti) {
      const sumM = $('skIntraSum');
      if (sumM) sumM.innerHTML = '5 日归一化 · ' + d.dates[0] + ' ~ ' + d.dates[d.dates.length - 1] + ' · 粗线为最新日';
      const vbM = $('skIntraVolBar');
      if (vbM) vbM.innerHTML = '';
      restoreQuoteStatus(); // 叠加态无单一日期，状态栏回到报价口径
      paintIntraday(d);
      return;
    }
    // 当日概况（价格小数位跟接口 decimal，量用累计量兜底）
    const dp = d.decimal || 2;
    const rows = d.data, preClose = d.preClose || rows[0].p;
    const o = rows[0].p, cl = rows[rows.length - 1].p;
    const hi = Math.max.apply(null, rows.map(x => x.h || x.p)), lo = Math.min.apply(null, rows.map(x => x.l || x.p));
    const amt = rows.reduce((s, x) => s + (x.amt || 0), 0);
    const chg = preClose ? (cl - preClose) / preClose * 100 : 0;
    const col = chg >= 0 ? '#f23645' : '#089981';
    const sum = $('skIntraSum');
    if (sum) sum.innerHTML = `${esc(d.date)} · 开 ${o.toFixed(dp)} 高 ${hi.toFixed(dp)} 低 ${lo.toFixed(dp)} 收 <b style="color:${col}">${cl.toFixed(dp)}</b> <b style="color:${col}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</b> · 额 ${(amt / 1e8).toFixed(2)} 亿`;
    applyIntraStatus(d);
    paintIntraday(d);
  }

  /* 涨跌幅限制：北交所 30%、科创/创业板 20%、主板 ST 5%、其余 10% */
  function limitRate(code, name) {
    const c = String(code || '');
    const num = c.split('.')[0], suf = c.split('.')[1] || '';
    if (suf === 'BJ') return 0.30;
    if (num.indexOf('688') === 0) return 0.20;
    if (suf === 'SZ' && (num.indexOf('300') === 0 || num.indexOf('301') === 0)) return 0.20;
    if (/ST/i.test(name || '')) return 0.05;
    return 0.10;
  }

  /* 量能结构：开盘30分 / 上午余下 / 午后 / 尾盘30分 的量与占比 */
  function renderVolBar(d) {
    const bar = $('skIntraVolBar');
    if (!bar) return;
    const rows = d.data || [];
    const segs = [
      { n: '开盘30分', a: '09:30', b: '10:00', c: 'rgba(74,158,255,0.90)' },
      { n: '上午余下', a: '10:01', b: '11:30', c: 'rgba(74,158,255,0.58)' },
      { n: '午后', a: '13:00', b: '14:29', c: 'rgba(240,185,11,0.72)' },
      { n: '尾盘30分', a: '14:30', b: '15:00', c: 'rgba(154,160,170,0.78)' }
    ];
    const tot = rows.reduce((s, r) => s + (r.vol || 0), 0) || 1;
    const items = segs.map(s => {
      const v = rows.filter(r => r.t >= s.a && r.t <= s.b).reduce((x, r) => x + (r.vol || 0), 0);
      return { n: s.n, c: s.c, v: v, pct: v / tot * 100 };
    });
    bar.innerHTML =
      '<div class="vb-stack">' + items.map(i => '<i class="vb-seg" style="width:' + i.pct.toFixed(2) + '%;background:' + i.c + '"></i>').join('') + '</div>' +
      '<div class="vb-leg">' + items.map(i => '<span><i class="vb-dot" style="background:' + i.c + '"></i>' + i.n +
        ' ' + (i.v / 10000).toFixed(2) + '万手 · ' + i.pct.toFixed(1) + '%</span>').join('') + '</div>';
  }

  function paintIntraday(d) {
    const box = $('skIntraChart');
    if (!box) return;
    if (S.intraMode === 'multi') return paintMulti(d);
    S.intraData = d;
    disposeIntraday();
    box.innerHTML = '';
    const c = echarts.init(box, null, { renderer: 'canvas' });
    S.intraChart = c;
    const rows = d.data, preClose = d.preClose || rows[0].p;
    const dp = d.decimal || 2;
    const fx = v => Number(v || 0).toFixed(dp);
    const isK = S.intraMode === 'k1';
    const times = rows.map(r => r.t);
    const prices = rows.map(r => r.p);
    const avgs = rows.map(r => r.avg);
    const ohlc = rows.map(r => [r.o, r.p, r.l, r.h]);   // ECharts: 开、收、低、高
    const volData = rows.map((r, i) => {
      const up = isK ? r.p >= r.o : r.p >= (i ? rows[i - 1].p : preClose);
      return { value: r.vol || 0, itemStyle: { color: up ? '#f23645' : '#089981' } };
    });
    const hi = Math.max.apply(null, rows.map(r => r.h).concat([preClose]));
    const lo = Math.min.apply(null, rows.map(r => r.l).concat([preClose]));
    const pad = Math.max((hi - lo) * 0.10, Math.pow(10, -dp));
    const step = Math.max(1, Math.floor(times.length / 9));
    // 日内极值点（用分钟最高/最低，标出价格与时间）
    let hiI = 0, loI = 0;
    rows.forEach((r, i) => {
      if (r.h > rows[hiI].h) hiI = i;
      if (r.l < rows[loI].l) loI = i;
    });
    // 极值点靠左侧时把标签右移，避免压住 y 轴刻度
    const offX = (i) => (i / Math.max(1, rows.length - 1) < 0.28 ? 58 : 0);
    const markPts = [
      { coord: [times[hiI], rows[hiI].h], symbolSize: 6, itemStyle: { color: '#f23645' },
        label: { show: true, position: 'top', offset: [offX(hiI), -2], color: '#f23645', fontSize: 10,
                 backgroundColor: 'rgba(12,15,21,0.88)', padding: [1, 4], borderRadius: 2,
                 formatter: '高 ' + fx(rows[hiI].h) + ' ' + times[hiI] } },
      { coord: [times[loI], rows[loI].l], symbolSize: 6, itemStyle: { color: '#089981' },
        label: { show: true, position: 'bottom', offset: [offX(loI), 2], color: '#089981', fontSize: 10,
                 backgroundColor: 'rgba(12,15,21,0.88)', padding: [1, 4], borderRadius: 2,
                 formatter: '低 ' + fx(rows[loI].l) + ' ' + times[loI] } }
    ];
    const lim = limitRate(d.code, d.name);
    const upP = Math.round(preClose * (1 + lim) * Math.pow(10, dp)) / Math.pow(10, dp);
    const dnP = Math.round(preClose * (1 - lim) * Math.pow(10, dp)) / Math.pow(10, dp);
    const hitUp = rows.some(r => r.h >= upP - Math.pow(10, -dp));
    const hitDn = rows.some(r => r.l <= dnP + Math.pow(10, -dp));
    const mlData = [{ yAxis: preClose }];
    if (hitUp) {
      mlData.push({ yAxis: upP, lineStyle: { color: '#f23645', type: 'solid', width: 1 },
        label: { position: 'insideEndTop', color: '#f23645', fontSize: 10, formatter: '涨停 ' + fx(upP) } });
    }
    if (hitDn) {
      mlData.push({ yAxis: dnP, lineStyle: { color: '#089981', type: 'solid', width: 1 },
        label: { position: 'insideEndBottom', color: '#089981', fontSize: 10, formatter: '跌停 ' + fx(dnP) } });
    }
    const markLine = {
      silent: true, symbol: 'none',
      lineStyle: { type: 'dashed', color: '#7a7f8a', width: 1 },
      label: { position: 'insideEndTop', color: '#9aa0aa', fontSize: 10, formatter: '昨收 ' + fx(preClose) },
      data: mlData
    };
    const tip = (ps) => {
      const p0 = ps && ps[0];
      if (!p0) return '';
      const r = rows[p0.dataIndex];
      if (!r) return '';
      const ch = preClose ? (r.p - preClose) / preClose * 100 : 0;
      const cc = ch >= 0 ? '#f23645' : '#089981';
      let s = `${esc(d.date)} ${r.t}<br>开 ${fx(r.o)} 高 ${fx(r.h)} 低 ${fx(r.l)} ` +
              `<b style="color:${cc}">收 ${fx(r.p)} (${ch >= 0 ? '+' : ''}${ch.toFixed(2)}%)</b>` +
              `<br>均价 ${fx(r.avg)}<br>量 ${(r.vol / 10000).toFixed(2)} 万手 · 额 ${(r.amt / 1e8).toFixed(2)} 亿`;
      if (r.cumVol) s += `<br>累计 ${(r.cumVol / 10000).toFixed(1)} 万手 · ${(r.cumAmt / 1e8).toFixed(2)} 亿`;
      return s;
    };
    const markPoint = { symbol: 'circle', data: markPts };
    const priceSeries = isK
      ? { name: '1分钟K', type: 'candlestick', data: ohlc, barMaxWidth: 4,
          itemStyle: { color: '#f23645', color0: '#089981', borderColor: '#f23645', borderColor0: '#089981' },
          markLine: markLine, markPoint: markPoint }
      : { name: '价格', type: 'line', data: prices, showSymbol: false, smooth: false,
          lineStyle: { width: 1.4, color: '#e6e8ee' },
          areaStyle: { color: 'rgba(74,158,255,0.12)' }, z: 3,
          markLine: markLine, markPoint: markPoint };
    const series = [priceSeries];
    if (!isK) {
      series.push({ name: '均价', type: 'line', data: avgs, showSymbol: false, smooth: false,
                    lineStyle: { width: 1.2, color: '#f0b90b' }, z: 2 });
    }
    series.push({ name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volData, barMaxWidth: 3 });
    const option = {
      animation: false,
      backgroundColor: 'transparent',
      grid: [
        { left: 64, right: 62, top: 18, height: '60%' },
        { left: 64, right: 62, top: '74%', height: '19%' }
      ],
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', label: { backgroundColor: '#3a3f4b' } },
        backgroundColor: 'rgba(20,24,32,0.95)', borderColor: '#3a3f4b',
        textStyle: { color: '#d1d4dc', fontSize: 11 },
        formatter: tip
      },
      xAxis: [
        { type: 'category', data: times, gridIndex: 0, boundaryGap: isK,
          axisLine: { lineStyle: { color: '#3a3f4b' } }, axisTick: { show: false },
          axisLabel: { color: '#9aa0aa', fontSize: 10, interval: step, showMinLabel: true, showMaxLabel: true },
          splitLine: { show: false } },
        { type: 'category', data: times, gridIndex: 1, boundaryGap: isK,
          axisLine: { lineStyle: { color: '#3a3f4b' } }, axisTick: { show: false },
          axisLabel: { show: false }, splitLine: { show: false } }
      ],
      yAxis: [
        { gridIndex: 0, scale: true, splitNumber: 5, min: lo - pad, max: hi + pad,
          axisLabel: { color: '#9aa0aa', fontSize: 10, formatter: v => v.toFixed(dp) },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } } },
        { gridIndex: 1, splitNumber: 2,
          axisLabel: { color: '#9aa0aa', fontSize: 10, formatter: v => v >= 10000 ? (v / 10000).toFixed(1) + '万' : String(Math.round(v)) },
          splitLine: { show: false } }
      ],
      series: series
    };
    c.setOption(option);
    renderVolBar(d);
    requestAnimationFrame(() => { try { c.resize(); } catch (e) { /* 忽略 */ } });
    setTimeout(() => { try { if (S.intraChart === c) c.resize(); } catch (e) { /* 忽略 */ } }, 260);
  }

  /* 5 日归一化叠加：每日以当日昨收=100 归一，看 5 天的日内形态强弱 */
  function paintMulti(d) {
    const box = $('skIntraChart');
    if (!box) return;
    const arr = d.multi || [];
    if (!arr.length) {
      disposeIntraday();
      box.innerHTML = '<div class="sk-empty" style="padding:40px;text-align:center">该标的暂无可用于叠加的 5 日分时数据</div>';
      return;
    }
    S.intraData = d;
    disposeIntraday();
    box.innerHTML = '';
    const c = echarts.init(box, null, { renderer: 'canvas' });
    S.intraChart = c;
    const dp = d.decimal || 2;
    const COLORS = ['#8a93a3', '#4a9eff', '#f0b90b', '#089981', '#c56cf0'];
    const lastIdx = arr.length - 1;
    const times = arr[lastIdx].data.map(x => x.t);
    const series = arr.map((day, k) => ({
      name: day.date,
      type: 'line',
      itemStyle: { color: COLORS[k % COLORS.length] },
      showSymbol: false, smooth: false,
      lineStyle: { width: k === lastIdx ? 1.7 : 1.1, color: COLORS[k % COLORS.length] },
      data: day.data.map(x => +(x.p / day.preClose * 100).toFixed(3)),
      z: k === lastIdx ? 5 : 2,
      emphasis: { focus: 'series' }
    }));
    series[0].markLine = {
      silent: true, symbol: 'none',
      lineStyle: { type: 'dashed', color: '#7a7f8a', width: 1 },
      label: { position: 'insideEndTop', color: '#9aa0aa', fontSize: 10, formatter: '昨收=100' },
      data: [{ yAxis: 100 }]
    };
    const tip = (ps) => {
      const p0 = ps && ps[0];
      if (!p0) return '';
      const i = p0.dataIndex;
      let s = times[i] + '<br>';
      arr.forEach((day, k) => {
        const r = day.data[i];
        if (!r) return;
        const ch = (r.p / day.preClose * 100) - 100;
        const col = ch >= 0 ? '#f23645' : '#089981';
        s += `<span style="color:${COLORS[k % COLORS.length]}">■</span> ${day.date} ${r.p.toFixed(dp)} ` +
             `<b style="color:${col}">${ch >= 0 ? '+' : ''}${ch.toFixed(2)}%</b><br>`;
      });
      return s;
    };
    const option = {
      animation: false, backgroundColor: 'transparent',
      grid: [{ left: 64, right: 24, top: 40, height: '68%' }],
      legend: { top: 2, left: 6, right: 6, itemWidth: 10, itemHeight: 8, itemGap: 8,
        textStyle: { color: '#9aa0aa', fontSize: 10 }, data: series.map(x => x.name) },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#3a3f4b' } },
        backgroundColor: 'rgba(20,24,32,0.95)', borderColor: '#3a3f4b',
        textStyle: { color: '#d1d4dc', fontSize: 11 }, formatter: tip },
      xAxis: [{ type: 'category', data: times, boundaryGap: false,
        axisLine: { lineStyle: { color: '#3a3f4b' } }, axisTick: { show: false },
        axisLabel: { color: '#9aa0aa', fontSize: 10, interval: Math.max(1, Math.floor(times.length / 9)),
          showMinLabel: true, showMaxLabel: true }, splitLine: { show: false } }],
      yAxis: [{ scale: true, splitNumber: 5,
        axisLabel: { color: '#9aa0aa', fontSize: 10, formatter: v => (v - 100).toFixed(1) + '%' },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } } }],
      series: series
    };
    c.setOption(option);
    requestAnimationFrame(() => { try { c.resize(); } catch (e) { /* 忽略 */ } });
    setTimeout(() => { try { if (S.intraChart === c) c.resize(); } catch (e) { /* 忽略 */ } }, 260);
  }

  /* 事件日期回跳 K线：龙虎榜/涨跌停事件点击 → 切回 K线 tab 并定位该 bar */
  function jumpToKline(dateStr) {
    setView('kline');
    const idx = (S.bars || []).findIndex(b => fmtDate(b.timestamp) === dateStr);
    if (idx >= 0 && S.chart && typeof S.chart.scrollToDataIndex === 'function') {
      try { S.chart.scrollToDataIndex(idx, 0.5); } catch (e) { /* 忽略 */ }
    }
  }

  /* 板块 chip 联动：点板块 → 记录联动状态并跳转板块行业页 */
  function chipToSector(el) {
    if (typeof App === 'undefined' || !App.linkSector || !App.navigate) return;
    const type = el.dataset.ptype || 'industry';
    App.linkSector(type, el.dataset.plate, el.dataset.pname, '');
    App.navigate('sector');
  }

  /* 加载信息模块：CH 快模块先渲染首屏，akshare 慢模块后台补位（单模块 20s 超时，防网络挂起卡死整个面板） */
  async function loadStockInfo(code) {
    const box = $('skInfoPanel');
    if (!box) return;
    box.innerHTML = '<div class="sk-empty" style="padding:50px;text-align:center;color:var(--text-secondary)">正在加载个股信息中心…</div>';
    disposeInfoCharts();
    const cache = (S.infoCache[code] = S.infoCache[code] || {});
    async function grab(m) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 20000);
      try {
        const r = await fetch(`${API}/stock/info?code=${encodeURIComponent(code)}&module=${m}`, { signal: ctrl.signal });
        cache[m] = r.ok ? await r.json() : null;
      } catch (e) { cache[m] = null; } finally { clearTimeout(timer); }
    }
    const fast = INFO_FAST.filter(m => !cache[m]);
    await Promise.all(fast.map(grab));
    if (S.code !== code || S.view !== 'info') return; // 竞态：已换股/切走则丢弃
    renderInfoPanel(code); // 首屏：CH 模块全量 + 开源模块占位
    const slow = INFO_SLOW.filter(m => !cache[m]);
    if (slow.length) {
      await Promise.all(slow.map(grab));
      if (S.code !== code || S.view !== 'info') return;
      renderInfoPanel(code); // 开源模块到达后全量重渲染
    }
  }

  /* 通用 SVG 多线图（归一折线，如 bench 相对走势） */
  function skSvgLines(el, series, keys, colors, names, baseKey, normBase) {
    const data = (series || []).slice(-24);
    if (!data.length) { el.innerHTML = '<div class="sk-empty">无数据</div>'; return; }
    const w = 640, h = 150, pad = 8;
    let min = Infinity, max = -Infinity;
    data.forEach(d => keys.forEach(k => { if (d[k] != null) { min = Math.min(min, d[k]); max = Math.max(max, d[k]); } }));
    if (!isFinite(min)) { el.innerHTML = '<div class="sk-empty">无数据</div>'; return; }
    const span = (max - min) || 1;
    const X = i => pad + i * (w - pad * 2) / Math.max(1, data.length - 1);
    const Y = v => pad + (h - pad * 2) * (1 - (v - min) / span);
    let html = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="150" preserveAspectRatio="none" style="display:block">`;
    if (baseKey != null && normBase != null) {
      const yb = Y(normBase);
      html += `<line x1="${pad}" y1="${yb}" x2="${w - pad}" y2="${yb}" stroke="#888" stroke-dasharray="4,4" stroke-width="1" opacity="0.5"/>`;
    }
    keys.forEach((k, ki) => {
      const pts = data.map((d, i) => `${X(i).toFixed(1)},${Y(d[k]).toFixed(1)}`).join(' ');
      html += `<polyline points="${pts}" fill="none" stroke="${colors[ki]}" stroke-width="1.8" opacity="0.95"/>`;
    });
    html += '</svg><div style="display:flex;gap:14px;font-size:11px;color:var(--text-secondary);margin-top:2px;flex-wrap:wrap">';
    names.forEach((n, i) => {
      html += `<span><i style="display:inline-block;width:10px;height:2px;background:${colors[i]};vertical-align:middle;margin-right:4px;border-radius:1px"></i>${esc(n)}</span>`;
    });
    if (baseKey != null && normBase != null) html += `<span style="margin-left:auto">虚线=起点100 · 近${data.length}个交易日</span>`;
    html += '</div>';
    el.innerHTML = html;
  }

  /* 分位标尺：当前分位点 + min/max 标签 */
  function skScale(el, curPct, minV, maxV, curV, unit) {
    const pct = Math.max(0, Math.min(100, curPct || 0));
    el.innerHTML = `<div class="sk-scale">
      <span class="lb" style="left:0">低</span>
      <span class="lb" style="left:50%">中</span>
      <span class="lb" style="left:100%">高</span>
      <div class="bar"></div>
      <span class="cur" style="left:${pct}%">◆ ${esc(curV != null ? curV : '-')}${unit || ''}（${(curPct || 0).toFixed(0)}分位）</span>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-secondary);margin-top:0">
      <span>min ${esc(minV != null ? minV : '-')}</span><span>max ${esc(maxV != null ? maxV : '-')}</span>
    </div>`;
  }

  /* ECharts 小图（统一回收） */
  function skChart(elId, option) {
    const el = $(elId);
    if (!el) return null;
    const old = echarts.getInstanceByDom(el);
    if (old) old.dispose();
    const c = echarts.init(el, null, { renderer: 'canvas' });
    /* 信息页首帧常在图区尚未进入视口时绘制，此时 ECharts 入场动画会被冻结在 0 值，
       表现为只剩坐标轴、柱体/曲线缺失；小图动画无信息价值，统一关闭 */
    option.animation = false;
    c.setOption(option);
    infoCharts.push(c);
    /* 信息页切换 tab 的瞬间容器尚未完成布局，首帧会按 0/错误宽度绘制（柱体/曲线丢失），
       等一帧后再 resize 一次，保证首屏就画对 */
    const fix = () => { try { if (!c.isDisposed()) c.resize(); } catch (e) { /* 忽略 */ } };
    if (window.requestAnimationFrame) window.requestAnimationFrame(fix);
    setTimeout(fix, 260);
    return c;
  }
  const SK_AXIS = { axisLine: { lineStyle: { color: '#3a3f4b' } }, axisLabel: { color: '#9aa0aa', fontSize: 10 }, splitLine: { lineStyle: { color: '#262b36' } } };

  /* ---------------- 渲染信息面板 ---------------- */
  function renderInfoPanel(code) {
    const box = $('skInfoPanel');
    if (!box) return;
    const cache = S.infoCache[code] || {};
    const D = m => ((cache[m] || {}).data) || {};
    const srcTag = (m, name) => `<span class="tag">${name} · ${((cache[m] || {}).source || 'ch').toUpperCase()}</span>`;
    const sector = D('sector');
    const stkName = S.name || code.split('.')[0];
    let html = '';
    /* ===== 核心卡：板块地位·趋势 ===== */
    const secs = sector.sectors || [];
    const conceptsAll = sector.conceptsAll || [];
    let chips = '';
    const chipHtml = (s, i) => {
      const cls = s.sectorPct > 0 ? 'pos' : (s.sectorPct < 0 ? 'neg' : 'flat');
      return `<span class="sk-chip" data-plate="${esc(s.plateCode)}" data-ptype="${esc(s.plateType)}" data-pname="${esc(s.plateName)}" data-idx="${i}" title="点击切换当前板块走势/对标">${esc(s.plateName)}<span class="chip-w ${cls}">${(s.weight || 0).toFixed(1)}%</span><span class="chip-w ${cls}">${s.sectorPct > 0 ? '+' : ''}${(s.sectorPct || 0).toFixed(2)}%</span><span class="chip-go" title="进入板块行业页">↗</span></span>`;
    };
    /* 分组渲染：一级行业 / 二级行业 / 题材（概念 top6 带指标 + 其余概念名） */
    const indSecs = secs.filter(s => s.plateType !== 'concept');
    const secGroups = [
      ['一级行业', indSecs.filter(s => s.plateLevel === 1)],
      ['二级行业', indSecs.filter(s => s.plateLevel === 2)],
      ['行业板块', indSecs.filter(s => !s.plateLevel)],
    ];
    let chipIdx = 0;
    secGroups.forEach(([label, list]) => {
      if (!list.length) return;
      const inner = list.map(s => chipHtml(s, chipIdx++)).join('');
      chips += `<div class="sk-chip-group"><span class="sk-chip-glabel">${label}</span><div class="sk-chip-wrap">${inner}</div></div>`;
    });
    const concFull = secs.filter(s => s.plateType === 'concept');
    const concNames = new Set(concFull.map(s => s.plateName));
    const concExtra = (conceptsAll || []).filter(c => !concNames.has(c && c.name)).slice(0, 30).map(c =>
      `<span class="sk-chip" data-plate="${esc(c.code)}" data-ptype="concept" data-pname="${esc(c.name)}" title="点击切换当前题材走势/对标">${esc(c.name)}</span>`).join('');
    const concChips = concFull.map(s => chipHtml(s, chipIdx++)).join('') + concExtra;
    if (concChips) {
      chips += `<div class="sk-chip-group"><span class="sk-chip-glabel">题材</span><div class="sk-chip-wrap">${concChips}</div></div>`;
    }
    const cur = secs[0];
    let coreBody = '';
    if (cur) {
      const up = v => v > 0 ? 'pos' : (v < 0 ? 'neg' : '');
      const sign = v => (v > 0 ? '+' : '') + (v != null ? v : '-');
      coreBody = `
      <div style="display:flex;gap:10px;margin-bottom:8px">
        <div class="sk-chip active cur-badge" style="cursor:default;border-color:var(--accent-blue);background:rgba(74,158,255,0.12)">当前板块 ${esc(cur.plateName)}</div>
        <div style="font-size:11px;color:var(--text-secondary);align-self:center">板块${cur.dateB ? esc(String(cur.dateB).slice(0, 10)) : '-'}涨跌 <b class="${up(cur.sectorPct)}">${sign(cur.sectorPct)}%</b> · 个股 ${sign(cur.stockPct)}% · 成分 ${cur.stockCnt} 只</div>
      </div>
      <div class="sk-badge-row">
        <span class="sk-badge">市值权重<b class="${cur.weight >= 15 ? 'pos' : ''}">${(cur.weight || 0).toFixed(1)}%</b></span>
        <span class="sk-badge">板块内涨幅<b>${cur.rank || '-'}/${cur.totalRank || '-'}</b></span>
        <span class="sk-badge">龙头定位<b>${esc(cur.leader || '-')}</b></span>
        <span class="sk-badge">涨跌贡献<b class="${up(cur.contrib)}">${sign(cur.contrib)}pp · 第${cur.contribRank || '-'}</b></span>
        <span class="sk-badge">成交占比<b>${(cur.amtShare || 0).toFixed(1)}%</b></span>
        <span class="sk-badge">资金占比<b>${cur.mfShare != null && cur.mfShare < 1000 ? cur.mfShare.toFixed(1) + '%' : '-'}</b></span>
        <span class="sk-badge">当日状态<b class="${cur.state === '领涨' || cur.state === '逆势' ? 'pos' : 'neg'}">${esc(cur.state || '-')}</b></span>
        <span class="sk-badge">RS1<b class="${up(cur.rs1)}">${sign(cur.rs1)}pp</b></span>
        <span class="sk-badge">RS5<b class="${up(cur.rs5)}">${sign(cur.rs5)}pp</b></span>
        <span class="sk-badge">RS20<b class="${up(cur.rs20)}">${sign(cur.rs20)}pp</b></span>
        <span class="sk-badge">20日相关<b>${cur.corr20 != null ? cur.corr20 : '-'}</b></span>
        <span class="sk-badge">趋势<b>${esc(cur.trend || '-')}</b></span>
        <span class="sk-badge">个股位置<b>${esc(cur.stkPos || '-')}</b></span>
      </div>
      <div id="skSpark" class="sk-mini" style="margin-top:10px;height:150px"></div>
      <div style="display:flex;gap:6px;justify-content:flex-end;margin-top:4px">
        <button class="sk-mode-btn active" data-mode="cum" style="background:rgba(74,158,255,0.15);border:1px solid var(--accent-blue);color:var(--text-primary);border-radius:4px;padding:2px 10px;font-size:11px;cursor:pointer">累计收益率</button>
        <button class="sk-mode-btn" data-mode="day" style="background:transparent;border:1px solid var(--border-color);color:var(--text-secondary);border-radius:4px;padding:2px 10px;font-size:11px;cursor:pointer">当日收益率</button>
      </div>`;
    } else {
      coreBody = '<div class="sk-empty">未查到该股的板块归属数据（CH 板块成员表可能未覆盖）</div>';
    }
    html += `<div class="sk-info-card sk-wide">
      <div class="sk-info-card-title">${INFO_TITLES.sector}${srcTag('sector', '多板块归属')}<span class="hint">红=涨/强 绿=跌/弱 · 权重=个股市值/板块市值</span></div>
      <div class="sk-sector-chips">${chips || '<div class="sk-empty">无板块归属</div>'}</div>
      ${coreBody}
    </div>`;

    /* ===== 个股主力资金净流入曲线（全宽；5/10/20/60/120 日 × 逐日/累计） ===== */
    html += `<div class="sk-info-card sk-wide">
      <div class="sk-info-card-title">${INFO_TITLES.mfcurve}${srcTag('mfcurve', '近120日')}<span class="hint">逐日=单日主力净流入（红=净流入 绿=净流出） · 累计=区间累计（白线 + 红绿面积 + 末端终值）</span></div>
      <div class="sk-mf-bar">
        <div class="sk-mf-seg" id="skMfDays">
          ${[5, 10, 20, 60, 120].map(d => `<button data-days="${d}" class="${d === 20 ? 'on' : ''}">${d}日</button>`).join('')}
        </div>
        <div class="sk-mf-seg" id="skMfMode">
          <button data-mode="daily" class="on">逐日主力净流入</button>
          <button data-mode="cum">累计主力净流入</button>
        </div>
        <span id="skMfSum" class="sk-mf-sum"></span>
      </div>
      <div id="skMfCurve" class="sk-mini" style="height:300px"></div>
    </div>`;

    /* ===== 10 信息模块（6 行布局：全宽行 / 3 卡等宽行，直接作为 grid 子级） ===== */

    /* ② 行情对标（与资金流/龙虎榜并排，1/3 宽） */
    const bench = D('bench');
    html += `<div class="sk-info-card"><div class="sk-info-card-title">${INFO_TITLES.bench}${srcTag('bench', '最新交易日')}<span class="hint">成交额/流通市值 占所属${bench.scope === '全市场' ? '全市场' : '板块'}合计（${esc((bench.date || '').slice(0, 10))}）</span></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 18px">
        <div><div style="font-size:11px;color:var(--text-secondary);margin:2px 0 4px">成交额占${bench.scope === '全市场' ? '全市场' : '板块'}：<b id="skBenchAmtPct" style="color:var(--accent-blue)">${bench.amtShare != null ? bench.amtShare + '%' : '—'}</b></div><div id="skBenchAmt" class="sk-mini" style="height:104px"></div></div>
        <div><div style="font-size:11px;color:var(--text-secondary);margin:2px 0 4px">流通市值占${bench.scope === '全市场' ? '全市场' : '板块'}：<b id="skBenchMvPct" style="color:var(--accent-blue)">${bench.mvShare != null ? bench.mvShare + '%' : '—'}</b></div><div id="skBenchMv" class="sk-mini" style="height:104px"></div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 18px;margin-top:6px;font-size:11px;color:var(--text-secondary)">
        <div class="sk-kv"><span class="k">个股成交额</span><span class="v">${bench.amt != null ? bench.amt + '亿' : '—'}</span></div>
        <div class="sk-kv"><span class="k">板块/全市场合计</span><span class="v">${bench.amtTotal != null ? bench.amtTotal + '亿' : '—'}</span></div>
        <div class="sk-kv"><span class="k">个股流通市值</span><span class="v">${bench.circMv != null ? bench.circMv + '亿' : '—'}</span></div>
        <div class="sk-kv"><span class="k">板块/全市场合计</span><span class="v">${bench.circTotal != null ? bench.circTotal + '亿' : '—'}</span></div>
      </div></div>`;

    /* ③ 资金流透视（与行情对标/龙虎榜并排，1/3 宽） */
    const mf = D('moneyflow');
    const mfDaily = mf.daily || [];
    html += `<div class="sk-info-card"><div class="sk-info-card-title">${INFO_TITLES.moneyflow}${srcTag('moneyflow', '近10日')}</div><div id="skMfChart" class="sk-mini" style="height:200px"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 18px;margin-top:6px">${mf.snapshot && mf.snapshot.date ? `<div class="sk-kv"><span class="k">北向持股(亿) ${esc((mf.snapshot.date || '').slice(0, 10))}</span><span class="v">${mf.snapshot.northHold != null ? mf.snapshot.northHold : '—'}</span></div><div class="sk-kv"><span class="k">融资余额(亿)</span><span class="v">${mf.snapshot.rzye != null ? mf.snapshot.rzye : '—'}</span></div>` : '<div class="sk-empty">无资金流数据</div>'}</div></div>`;

    /* ④ 龙虎榜·异动（与行情对标/资金流并排，1/3 宽） */
    const dr = D('dragon');
    const dragon = dr.dragon || [];
    const events = dr.events || [];
    html += `<div class="sk-info-card"><div class="sk-info-card-title">${INFO_TITLES.dragon}${srcTag('dragon', '近60日')}<span class="hint">点击日期回跳 K线</span></div>`;
    if (dragon.length) {
      html += `<table class="sk-info-table"><tr><th>日期</th><th>类型</th><th>涨跌幅</th><th>净买(亿)</th><th>买入(亿)</th><th>卖出(亿)</th><th>上榜原因</th><th>机构净买(亿)</th></tr>` +
        dragon.map(r => `<tr><td class="click" style="cursor:pointer;color:var(--accent-blue)" data-date="${esc(r.date)}">${esc(r.date)}</td><td>${esc(r.boardType === 'org' ? '机构' : '游资')}</td><td class="${r.chgPct > 0 ? 'pos' : 'neg'}">${r.chgPct > 0 ? '+' : ''}${r.chgPct}</td><td class="${r.netValue > 0 ? 'pos' : 'neg'}">${r.netValue}</td><td>${r.buyValue}</td><td>${r.sellValue}</td><td style="max-width:220px;overflow:hidden;text-overflow:ellipsis">${esc(r.limitReason || '-')}</td><td>${r.orgNetValue != null ? r.orgNetValue : '-'}</td></tr>`).join('') + '</table>';
    } else {
      html += '<div class="sk-empty">近8期无龙虎榜记录</div>';
    }
    if (events.length) {
      html += '<div class="sk-info-list" style="margin-top:8px">' + events.slice(0, 12).map(e =>
        `<div class="li click" data-date="${esc(e.date)}"><span class="d">${esc(e.date)}</span><span class="t">${esc(e.text)}</span><span class="${e.pct > 0 ? 'pos' : 'neg'}" style="font-size:11px">${e.pct ? (e.pct > 0 ? '+' : '') + e.pct + '%' : ''}</span><span class="src">↗ 回K线</span></div>`).join('') + '</div>';
    }
    html += '</div>';

    /* ⑤ 股东·质押（与估值分位/热度·地位并排，1/3 宽） */
    const hd = D('holder');
    html += `<div class="sk-info-card"><div class="sk-info-card-title">${INFO_TITLES.holder}${srcTag('holder', '户数/质押')}</div>
      <div style="font-size:11px;color:var(--text-secondary);margin:2px 0 4px">股东户数（万人）</div><div id="skHolderChart" class="sk-mini" style="height:75px"></div>
      <div style="font-size:11px;color:var(--text-secondary);margin:10px 0 4px">质押比例（%）</div><div id="skPledgeChart" class="sk-mini" style="height:75px"></div></div>`;

    /* ⑥ 估值分位（与股东·质押/热度·地位并排，1/3 宽） */
    const val = D('valuation');
    html += `<div class="sk-info-card"><div class="sk-info-card-title">${INFO_TITLES.valuation}${srcTag('valuation', '近5年')}<span class="hint">当前值 vs 历史分位</span></div>`;
    if (val.pe) {
      html += `<div style="font-size:11px;color:var(--text-secondary);margin:2px 0">PE(TTM) 近5年分布：</div><div id="skPeScale"></div>
      <div style="font-size:11px;color:var(--text-secondary);margin:14px 0 2px">PB 近5年分布：</div><div id="skPbScale"></div>`;
    } else {
      html += '<div class="sk-empty">无估值数据</div>';
    }
    html += '</div>';

    /* ⑦ 热度·地位（与股东·质押/估值分位并排，1/3 宽） */
    const ht = D('hot');
    html += `<div class="sk-info-card"><div class="sk-info-card-title">${INFO_TITLES.hot}${srcTag('hot', '全市场')}<span class="hint">排名越小越热 · 市值/成交额=当日全市场</span></div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:8px">
        <div class="sk-kv"><span class="k">同花顺热榜</span><span class="v">${ht.hotRankCur != null ? '第' + ht.hotRankCur + '名' : '未上榜'}</span></div>
        <div class="sk-kv"><span class="k">总市值排名</span><span class="v">${ht.mvRank ? '第' + ht.mvRank + '/' + (ht.total || '-') : '-'}</span></div>
        <div class="sk-kv"><span class="k">成交额排名</span><span class="v">${ht.amtRank ? '第' + ht.amtRank + '/' + (ht.total || '-') : '-'}</span></div>
      </div><div id="skHotChart" class="sk-mini" style="height:130px"></div><div id="skHotCap" style="font-size:11px;color:var(--text-secondary);margin-top:2px"></div></div>`;

    /* ⑧ 财务快照（全宽行） */
    const fin = D('finance');
    const quarters = fin.quarters || [];
    html += `<div class="sk-info-card sk-wide"><div class="sk-info-card-title">${INFO_TITLES.finance}${srcTag('finance', '累计口径')}<span class="hint">单位：亿元 · 同比=去年同期累计</span></div>`;
    if (quarters.length) {
      html += `<table class="sk-info-table sk-fin" style="white-space:normal"><tr><th>报告期</th><th>营收</th><th>净利</th><th>营业利润</th><th>EPS</th><th>ROE</th><th>毛利率</th><th>净利率</th><th>负债率</th><th>营收同比</th><th>净利同比</th><th>总资产</th><th>总负债</th><th>净资产</th><th>货币资金</th><th>应收款</th><th>存货</th><th>经营现金流</th><th>投资现金流</th><th>筹资现金流</th></tr>` +
        quarters.map(q => `<tr><td>${esc(q.endDate)}</td><td>${q.revenue != null ? q.revenue : '-'}</td><td>${q.netIncome != null ? q.netIncome : '-'}</td><td>${q.operateProfit != null ? q.operateProfit : '-'}</td><td>${q.basicEps != null ? q.basicEps : '-'}</td><td>${q.roe != null ? q.roe + '%' : '-'}</td><td>${q.grossMargin != null ? q.grossMargin + '%' : '-'}</td><td>${q.netMargin != null ? q.netMargin + '%' : '-'}</td><td>${q.debtRatio != null ? q.debtRatio + '%' : '-'}</td><td class="${q.trYoy > 0 ? 'pos' : (q.trYoy < 0 ? 'neg' : '')}">${q.trYoy != null ? (q.trYoy > 0 ? '+' : '') + q.trYoy + '%' : '-'}</td><td class="${q.netprofitYoy > 0 ? 'pos' : (q.netprofitYoy < 0 ? 'neg' : '')}">${q.netprofitYoy != null ? (q.netprofitYoy > 0 ? '+' : '') + q.netprofitYoy + '%' : '-'}</td><td>${q.totalAssets != null ? q.totalAssets : '-'}</td><td>${q.totalLiab != null ? q.totalLiab : '-'}</td><td>${q.equity != null ? q.equity : '-'}</td><td>${q.moneyCap != null ? q.moneyCap : '-'}</td><td>${q.accountsReceiv != null ? q.accountsReceiv : '-'}</td><td>${q.inventories != null ? q.inventories : '-'}</td><td>${q.cashAct != null ? q.cashAct : '-'}</td><td>${q.invAct != null ? q.invAct : '-'}</td><td>${q.finAct != null ? q.finAct : '-'}</td></tr>`).join('') + '</table>';
    } else {
      html += '<div class="sk-empty">无财务数据</div>';
    }
    html += '</div>';

    /* ⑨ 解禁·分红（全宽行） */
    const sh = D('share');
    const divs = sh.dividends || [], rests = sh.restricted || [];
    const shLoading = cache['share'] === undefined;
    const emptyDiv = shLoading ? '<div class="sk-empty">分红记录加载中…</div>' : '<div class="sk-empty">无分红记录</div>';
    const emptyRest = shLoading ? '<div class="sk-empty">解禁记录加载中…</div>' : '<div class="sk-empty">无解禁记录</div>';
    html += `<div class="sk-info-card sk-wide"><div class="sk-info-card-title">${INFO_TITLES.share}${srcTag('share', '开源补位')}<span class="hint">分红送配 + 限售解禁 · 单位：股/万元</span></div>`;
    const shareRows = [];
    (divs || []).slice(0, 8).forEach(d => shareRows.push({
      date: d.period, type: '分红', plan: d.plan || '-', rec: d.recordDate || '-', ex: d.exDate || '-',
      num: '-', mkt: '-', ratio: '-'
    }));
    (rests || []).slice(0, 8).forEach(r => shareRows.push({
      date: r.date, type: '解禁', plan: '-', rec: '-', ex: '-',
      num: r.num || '-', mkt: r.mktVal || '-', ratio: r.ratio || '-'
    }));
    shareRows.sort((a, b) => (a.date < b.date ? 1 : -1));
    const shareRowsHtml = shareRows.map(x => {
      const tcol = x.type === '分红' ? 'var(--accent-blue)' : '#ffd166';
      return '<tr><td>' + esc(x.date) + '</td><td><span style="color:' + tcol + '">' + x.type + '</span></td><td>' + esc(x.plan) +
        '</td><td>' + esc(x.rec) + '</td><td>' + esc(x.ex) + '</td><td>' + esc(x.num) + '</td><td>' + esc(x.mkt) + '</td><td>' + esc(x.ratio) + '</td></tr>';
    }).join('');
    html += shareRows.length
      ? '<table class="sk-info-table"><tr><th>日期</th><th>类型</th><th>方案/内容</th><th>股权登记日</th><th>除权除息日</th><th>解禁数量</th><th>解禁市值</th><th>占总股本</th></tr>' + shareRowsHtml + '</table>'
      : (shLoading ? '<div class="sk-empty">分红/解禁加载中…</div>' : '<div class="sk-empty">无分红/解禁记录</div>');
    html += '</div>';

    /* ⑩ 公告·舆情（全宽行） */
    const nw = D('news');
    const newsItems = nw.items || [];
    const newsLoading = cache['news'] === undefined;
    html += `<div class="sk-info-card sk-wide"><div class="sk-info-card-title">${INFO_TITLES.news}${srcTag('news', '公告+新闻')}<span class="hint">开源补位：东财公告/新闻</span></div>`;
    html += newsItems.length
      ? `<div class="sk-info-list">` + newsItems.slice(0, 12).map(it => {
        const t = esc(it.title);
        const titleHtml = it.url && /^https?:\/\//i.test(it.url) ? `<a class="nlink" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${t}</a>` : t;
        return `<div class="li"><span class="d">${esc(it.date)}</span><span class="t">${titleHtml}</span><span class="src">${esc(it.type)}${it.source ? ' · ' + esc(it.source) : ''}</span></div>`;
      }).join('') + '</div>'
      : (newsLoading ? '<div class="sk-empty">公告/新闻加载中…</div>' : '<div class="sk-empty">暂无公告/新闻（akshare 不可达或当日无数据）</div>');
    html += '</div>';

    box.innerHTML = html;
    bindInfoEvents(box);
    paintInfoCharts(cache, code);
    initSparkVsIndex(code);
    /* 信息页切 tab / 首次渲染时容器尚未完成布局，首帧 ECharts 会按错误尺寸绘制（柱体/曲线缺失），
       布局稳定后再整体重绘一次 */
    if (S._repaintT) clearTimeout(S._repaintT);
    S._repaintT = setTimeout(() => {
      const cc = S.infoCache[code];
      if (cc && document.getElementById('skMfCurve')) paintInfoCharts(cc, code);
    }, 360);
  }

  /* 核心卡趋势数据：板块 vs 大盘 · 收益走势（与板块行业页 renderSectorRel 同类型）
     板块=个股主行业（sector.sectors[0]，可传 plateOverride 切换），大盘=沪深300 + 上证指数；累计/当日双模式 */
  async function initSparkVsIndex(code, plateOverride) {
    try {
      const cache = S.infoCache[code] || (S.infoCache[code] = {});
      const sector = ((cache.sector || {}).data) || {};
      const plate = plateOverride || (sector.sectors || [])[0];
      if (!plate || !plate.plateCode) { if (cache._spark === undefined) cache._spark = null; return; }
      const [secRes, hsRes, shRes, stkRes] = await Promise.all([
        fetch(`${API}/ch/sector-kline?code=${encodeURIComponent(plate.plateCode)}&days=120`).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch(`${API}/index/000300/kline?days=200`).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch(`${API}/index/000001/kline?days=200`).then(r => r.ok ? r.json() : null).catch(() => null),
        fetch(`${API}/stock/kline?code=${encodeURIComponent(code)}&days=120&freq=daily`).then(r => r.ok ? r.json() : null).catch(() => null)
      ]);
      if (!secRes || !secRes.length) { if (cache._spark === undefined) cache._spark = null; return; }
      const dates = secRes.map(x => x.date);
      const base = secRes[0].close || 1;
      const platePct = secRes.map(x => +((x.changePct || 0) || 0).toFixed(2));
      const plateCum = secRes.map(x => +((x.close / base - 1) * 100).toFixed(2));
      const mkIdx = (idxArr) => {
        const pctByDate = {}, cumByDate = {};
        const basePos = idxArr.findIndex(x => x.date === dates[0]);
        const baseClose = basePos >= 0 ? idxArr[basePos].close : (idxArr[0] ? idxArr[0].close : 1);
        for (let i = 0; i < idxArr.length; i++) {
          const d = idxArr[i].date;
          pctByDate[d] = i === 0 ? 0 : +((idxArr[i].close / idxArr[i - 1].close - 1) * 100).toFixed(2);
          cumByDate[d] = +((idxArr[i].close / baseClose - 1) * 100).toFixed(2);
        }
        return { pct: dates.map(d => pctByDate[d] !== undefined ? pctByDate[d] : null), cum: dates.map(d => cumByDate[d] !== undefined ? cumByDate[d] : null) };
      };
      const hs = mkIdx(hsRes || []);
      const sh = mkIdx(shRes || []);
      const stkArr = (stkRes && stkRes.kline) ? stkRes.kline : [];
      const stk = mkIdx(stkArr);
      cache._spark = { dates: dates.map(d => d.slice(5)), plateName: plate.plateName, platePct, plateCum,
        hs300Pct: hs.pct, hs300Cum: hs.cum, shPct: sh.pct, shCum: sh.cum,
        stkName: (stkRes && stkRes.name) || S.name || code, stkPct: stk.pct, stkCum: stk.cum };
      if (document.getElementById('skSpark')) paintInfoCharts(cache, code);
    } catch (e) { /* 忽略：趋势图保持加载占位 */ }
  }

  /* 信息卡图表绘制（在 DOM 就绪后调用） */
  function paintInfoCharts(cache, code) {
    const D = m => ((cache[m] || {}).data) || {};
    const bench = D('bench');
    /* 核心卡趋势：板块 vs 大盘 · 收益走势对比（与板块行业页同类型：板块金线 + 沪深300/上证细线，日收益率/累计收益双模式） */
    const sparkEl = $('skSpark');
    const sparkData = (S.infoCache[code] && S.infoCache[code]._spark) || null;
    if (sparkEl && sparkData && sparkData.dates.length) {
      const sparkMode = (S.infoCache[code] && S.infoCache[code]._sparkMode) || 'cum';
      const isCum = sparkMode === 'cum';
      const mk = (arr, m) => m === 'cum' ? arr : arr.map((v, i) => +(i === 0 ? 0 : (arr[i] - arr[i - 1]) / Math.abs(arr[i - 1]) * 100).toFixed(2));
      const yFmt = isCum ? v => v.toFixed(2) + '%' : v => (v > 0 ? '+' : '') + v.toFixed(2) + '%';
      skChart('skSpark', {
        backgroundColor: 'transparent', grid: { left: 46, right: 14, top: 26, bottom: 22 },
        tooltip: { trigger: 'axis', backgroundColor: '#1e2230', borderColor: '#3a3f4b', textStyle: { color: '#d7dae0', fontSize: 11 },
          valueFormatter: v => v === null || v === undefined ? '-' : Number(v).toFixed(2) + '%' },
        legend: { data: [sparkData.plateName || '行业板块', sparkData.stkName || code, '沪深300', '上证指数'], textStyle: { color: '#9aa0aa', fontSize: 11 }, top: 0 },
        xAxis: { type: 'category', data: sparkData.dates, boundaryGap: false, ...SK_AXIS, axisLabel: { color: '#9aa0aa', fontSize: 9, interval: 5 } },
        yAxis: { type: 'value', scale: true, ...SK_AXIS, axisLabel: { color: '#9aa0aa', fontSize: 9, formatter: yFmt } },
        series: [
          { name: sparkData.plateName || '行业板块', type: 'line', symbol: 'none', data: isCum ? sparkData.plateCum : sparkData.platePct, smooth: true, lineStyle: { width: 2.5, color: '#f0b90b' }, itemStyle: { color: '#f0b90b' },
            markLine: { silent: true, symbol: 'none', lineStyle: { color: '#888', type: 'dashed', width: 1 }, data: [{ yAxis: 0 }], label: { show: false } } },
          { name: sparkData.stkName || code, type: 'line', symbol: 'none', data: isCum ? sparkData.stkCum : sparkData.stkPct, smooth: true, lineStyle: { width: 1.8, color: '#4a9eff' }, itemStyle: { color: '#4a9eff' } },
          { name: '沪深300', type: 'line', symbol: 'none', data: isCum ? sparkData.hs300Cum : sparkData.hs300Pct, smooth: true, lineStyle: { width: 1.4, color: '#b48cff' }, itemStyle: { color: '#b48cff' } },
          { name: '上证指数', type: 'line', symbol: 'none', data: isCum ? sparkData.shCum : sparkData.shPct, smooth: true, lineStyle: { width: 1.4, color: '#089981' }, itemStyle: { color: '#089981' } }
        ]
      });
    } else if (sparkEl) {
      sparkEl.innerHTML = '<div class="sk-empty">板块 vs 大盘 走势加载中…</div>';
    }
    /* 行情对标：成交额占比 + 流通市值占比（圆环 + 中心数值） */
    if ($('skBenchAmt') && bench.amtShare != null) {
      skChart('skBenchAmt', {
        backgroundColor: 'transparent',
        tooltip: { formatter: () => '成交额占' + (bench.scope === '全市场' ? '全市场' : '板块') + ' ' + bench.amtShare + '%' },
        series: [{ type: 'pie', radius: ['68%', '84%'], center: ['50%', '50%'], silent: true, label: { show: false }, data: [
          { value: bench.amtShare, itemStyle: { color: '#4a9eff' } },
          { value: +(100 - bench.amtShare).toFixed(2), itemStyle: { color: '#22262f' } }] }],
        graphic: [{ type: 'text', left: 'center', top: '38%', style: { text: bench.amtShare + '%', fill: '#4a9eff', fontSize: 15, fontWeight: 600, textAlign: 'center' } },
          { type: 'text', left: 'center', top: '56%', style: { text: '占' + (bench.scope === '全市场' ? '全市场' : '板块'), fill: '#9aa0aa', fontSize: 10, textAlign: 'center' } }]
      });
    } else if ($('skBenchAmt')) {
      $('skBenchAmt').innerHTML = '<div class="sk-empty">无成交额占比数据</div>';
    }
    if ($('skBenchMv') && bench.mvShare != null) {
      skChart('skBenchMv', {
        backgroundColor: 'transparent',
        tooltip: { formatter: () => '流通市值占' + (bench.scope === '全市场' ? '全市场' : '板块') + ' ' + bench.mvShare + '%' },
        series: [{ type: 'pie', radius: ['68%', '84%'], center: ['50%', '50%'], silent: true, label: { show: false }, data: [
          { value: bench.mvShare, itemStyle: { color: '#f0b90b' } },
          { value: +(100 - bench.mvShare).toFixed(2), itemStyle: { color: '#22262f' } }] }],
        graphic: [{ type: 'text', left: 'center', top: '38%', style: { text: bench.mvShare + '%', fill: '#f0b90b', fontSize: 15, fontWeight: 600, textAlign: 'center' } },
          { type: 'text', left: 'center', top: '56%', style: { text: '占' + (bench.scope === '全市场' ? '全市场' : '板块'), fill: '#9aa0aa', fontSize: 10, textAlign: 'center' } }]
      });
    } else if ($('skBenchMv')) {
      $('skBenchMv').innerHTML = '<div class="sk-empty">无流通市值占比数据</div>';
    }
    /* 个股主力资金净流入曲线：5/10/20/60/120 日 × 逐日/累计 */
    (function () {
      const el = $('skMfCurve');
      if (!el) return;
      const daysEl = $('skMfDays'), modeEl = $('skMfMode'), sumEl = $('skMfSum');
      const days = S.infoMfDays || 20, mode = S.infoMfMode || 'daily';
      if (daysEl) daysEl.querySelectorAll('button').forEach(b => {
        b.className = (+b.dataset.days === days) ? 'on' : '';
        b.onclick = () => { S.infoMfDays = +b.dataset.days; paintInfoCharts(cache, code); };
      });
      if (modeEl) modeEl.querySelectorAll('button').forEach(b => {
        b.className = (b.dataset.mode === mode) ? 'on' : '';
        b.onclick = () => { S.infoMfMode = b.dataset.mode; paintInfoCharts(cache, code); };
      });
      const mfAll = (D('mfcurve').daily || []);
      const emptyBox = (txt) => {
        /* 先销毁该容器上残留的 ECharts 实例，否则旧 canvas 会盖住空态文字 */
        try { const old = echarts.getInstanceByDom(el); if (old) old.dispose(); } catch (e) { /* 忽略 */ }
        el.style.height = '120px';
        el.innerHTML = '<div style="height:120px;display:flex;align-items:center;justify-content:center;font-size:13px;color:var(--text-primary);opacity:.8;border:1px dashed var(--border-color);border-radius:4px;background:rgba(255,255,255,0.02)">' + txt + '</div>';
      };
      if (!mfAll.length) { emptyBox('无资金流数据（该股未接入 money_flow）'); if (sumEl) sumEl.textContent = ''; return; }
      const arr = mfAll.slice(-days);
      const dates = arr.map(d => d.date.slice(5));
      const vals = arr.map(d => +(d.main || 0));
      const total = vals.reduce((a, b) => a + b, 0);
      if (sumEl) sumEl.textContent = '区间合计 ' + (total >= 0 ? '+' : '') + total.toFixed(1) + ' 亿';
      if (vals.every(v => v === 0)) {
        emptyBox('该股区间主力净流入全为 0（北交所 / 停牌 / 数据源缺失）');
        if (sumEl) sumEl.textContent = '';
        return;
      }
      el.style.height = '300px';
      const axis = Object.assign({}, SK_AXIS, { axisLabel: { color: '#9aa0aa', fontSize: 10, interval: Math.max(0, Math.ceil(arr.length / 9) - 1) } });
      if (mode === 'cum') {
        let acc = 0; const cumV = vals.map(v => { acc += v; return +acc.toFixed(2); });
        const pos = cumV.map(v => v > 0 ? v : 0), neg = cumV.map(v => v < 0 ? v : 0);
        skChart('skMfCurve', {
          backgroundColor: 'transparent', grid: { left: 56, right: 82, top: 18, bottom: 26 },
          tooltip: {
            trigger: 'axis', backgroundColor: '#1e2230', borderColor: '#3a3f4b', textStyle: { color: '#d7dae0', fontSize: 11 },
            formatter: ps => {
              const p = (ps || []).filter(x => x.seriesName && x.seriesName.indexOf('§') < 0)[0];
              if (!p) return '';
              const i = p.dataIndex;
              return dates[i] + '<br/>累计 ' + (cumV[i] >= 0 ? '+' : '') + cumV[i].toFixed(2) + ' 亿<br/>当日 ' + (vals[i] >= 0 ? '+' : '') + vals[i].toFixed(2) + ' 亿';
            }
          },
          xAxis: { type: 'category', data: dates, boundaryGap: false, ...axis },
          yAxis: { type: 'value', name: '累计(亿)', ...axis },
          series: [
            { name: '累计面积正§', type: 'line', data: pos, symbol: 'none', smooth: true, lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(242,54,69,0.22)' }, silent: true, tooltip: { show: false }, z: 1 },
            { name: '累计面积负§', type: 'line', data: neg, symbol: 'none', smooth: true, lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(8,153,129,0.22)' }, silent: true, tooltip: { show: false }, z: 1 },
            { name: '累计主力净流入', type: 'line', data: cumV, symbol: 'none', smooth: true, z: 3,
              lineStyle: { color: '#e6e8ee', width: 2.2 },
              markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: 'rgba(230,232,238,0.45)', width: 1, type: 'solid' }, data: [{ yAxis: 0 }] },
              endLabel: { show: true, distance: 6, fontSize: 11, color: '#c9cdd6', formatter: p => (p.value >= 0 ? '+' : '') + Number(p.value).toFixed(1) + '亿' } }
          ]
        });
      } else {
        skChart('skMfCurve', {
          backgroundColor: 'transparent', grid: { left: 56, right: 18, top: 18, bottom: 26 },
          tooltip: {
            trigger: 'axis', backgroundColor: '#1e2230', borderColor: '#3a3f4b', textStyle: { color: '#d7dae0', fontSize: 11 },
            formatter: ps => {
              const p = (ps || [])[0]; if (!p) return '';
              const i = p.dataIndex;
              return dates[i] + '<br/>主力净流入 ' + (vals[i] >= 0 ? '+' : '') + vals[i].toFixed(2) + ' 亿';
            }
          },
          xAxis: { type: 'category', data: dates, ...axis },
          yAxis: { type: 'value', name: '当日(亿)', ...axis },
          series: [{
            name: '主力净流入', type: 'bar', barMaxWidth: 18,
            data: vals.map(v => ({ value: v, itemStyle: { color: v >= 0 ? '#f23645' : '#089981', opacity: 0.9 } }))
          }]
        });
      }
    })();

    /* 资金流 */
    const mf = D('moneyflow');
    if ((mf.daily || []).length && $('skMfChart')) {
      const dates = mf.daily.map(d => d.date.slice(5));
      skChart('skMfChart', {
        backgroundColor: 'transparent', grid: { left: 44, right: 12, top: 24, bottom: 22 },
        tooltip: { trigger: 'axis', backgroundColor: '#1e2230', borderColor: '#3a3f4b', textStyle: { color: '#d7dae0', fontSize: 11 }, valueFormatter: v => v + '亿' },
        legend: { data: ['超大单', '大单', '中单', '小单'], textStyle: { color: '#9aa0aa', fontSize: 10 }, top: 0 },
        xAxis: { type: 'category', data: dates, ...SK_AXIS },
        yAxis: { type: 'value', name: '亿', ...SK_AXIS },
        series: ['elg', 'lg', 'md', 'sm'].map((k, i) => ({
          name: ['超大单', '大单', '中单', '小单'][i], type: 'bar', stack: 'flow', barMaxWidth: 14,
          data: mf.daily.map(d => d[k]), itemStyle: { color: ['#f23645', '#ff7b72', '#4a9eff', '#7bd0a1'][i], opacity: 0.85 }
        })).concat({
          name: '主力净流入', type: 'line', symbol: 'none', lineStyle: { width: 1.4, color: '#ffd166' },
          data: mf.daily.map(d => d.main), itemStyle: { color: '#ffd166' }
        })
      });
    }
    /* 股东户数 / 质押 */
    const hd = D('holder');
    if ((hd.holders || []).length && $('skHolderChart')) {
      const rev = hd.holders.slice().reverse();
      skChart('skHolderChart', {
        backgroundColor: 'transparent', grid: { left: 48, right: 14, top: 14, bottom: 24 },
        tooltip: { trigger: 'axis', backgroundColor: '#1e2230', borderColor: '#3a3f4b', textStyle: { color: '#d7dae0', fontSize: 11 } },
        xAxis: { type: 'category', data: rev.map(h => h.endDate.slice(2)), ...SK_AXIS, axisLabel: { color: '#9aa0aa', fontSize: 9, rotate: 20, interval: 0 } },
        yAxis: { type: 'value', ...SK_AXIS, axisLabel: { color: '#9aa0aa', fontSize: 10, formatter: v => v >= 10000 ? (v / 10000).toFixed(1) + '万' : String(v) } },
        series: [{ name: '股东户数', type: 'line', smooth: true, symbol: 'circle', symbolSize: 4, data: rev.map(h => h.holderNum), lineStyle: { color: '#4a9eff' }, itemStyle: { color: '#4a9eff' } }]
      });
    }
    if ((hd.pledges || []).length && $('skPledgeChart')) {
      const rev = hd.pledges.slice().reverse();
      skChart('skPledgeChart', {
        backgroundColor: 'transparent', grid: { left: 48, right: 14, top: 14, bottom: 24 },
        tooltip: { trigger: 'axis', backgroundColor: '#1e2230', borderColor: '#3a3f4b', textStyle: { color: '#d7dae0', fontSize: 11 }, valueFormatter: v => v + '%' },
        xAxis: { type: 'category', data: rev.map(p => p.endDate.slice(2)), ...SK_AXIS, axisLabel: { color: '#9aa0aa', fontSize: 9, rotate: 20, interval: 0 } },
        yAxis: { type: 'value', ...SK_AXIS, axisLabel: { color: '#9aa0aa', fontSize: 10, formatter: v => v + '%' } },
        series: [{ name: '质押比例', type: 'bar', barMaxWidth: 14, data: rev.map(p => p.ratio), itemStyle: { color: '#ffd166', opacity: 0.85 } }]
      });
    }
    /* 估值分位标尺 */
    const val = D('valuation');
    if (val.pe && $('skPeScale')) {
      skScale($('skPeScale'), val.pePct, val.pe.min, val.pe.max, val.peCur);
    }
    if (val.pb && $('skPbScale')) {
      skScale($('skPbScale'), val.pbPct, val.pb.min, val.pb.max, val.pbCur);
    }
    /* 热度折线（rank 越小越热 → y 反转） */
    const ht = D('hot');
    if ((ht.hotSeries || []).length && $('skHotChart')) {
      const rev = ht.hotSeries.slice().reverse();
      const allNull = rev.every(x => x.rank == null);
      skChart('skHotChart', {
        backgroundColor: 'transparent', grid: { left: 40, right: 16, top: 10, bottom: 22 },
        tooltip: { trigger: 'axis', backgroundColor: '#1e2230', borderColor: '#3a3f4b', textStyle: { color: '#d7dae0', fontSize: 11 } },
        xAxis: { type: 'category', data: rev.map(x => x.date.slice(5)), ...SK_AXIS, axisLabel: { color: '#9aa0aa', fontSize: 9, interval: 2 } },
        yAxis: { type: 'value', inverse: true, min: 1, ...SK_AXIS, axisLabel: { color: '#9aa0aa', fontSize: 9 } },
        series: [{ name: '热榜排名', type: 'line', data: rev.map(x => x.rank), smooth: true, symbol: 'circle', symbolSize: 4, connectNulls: false,
          lineStyle: { width: 1.6, color: allNull ? '#3a3f4b' : '#f23645', type: allNull ? 'dashed' : 'solid' }, itemStyle: { color: allNull ? '#5a5f6b' : '#f23645' },
          areaStyle: allNull ? undefined : { color: 'rgba(242,54,69,0.10)' } }]
        .concat(allNull ? [{
          type: 'line', name: '未上榜参考', data: rev.map(() => 30), symbol: 'none', lineStyle: { color: '#2a2f3a', type: 'dashed', width: 1 }, tooltip: { show: false }, silent: true
        }] : []),
        graphic: allNull ? [{ type: 'text', left: 'center', top: 'middle', style: { text: '近期未进入同花顺热榜 TOP100', fill: '#9aa0aa', fontSize: 12 } }] : []
      });
      const cap = $('skHotCap');
      if (cap) cap.textContent = '近' + rev.length + '期同花顺热榜排名（数值越小越热） · 最新排名 ' + (ht.hotRankCur != null ? '第' + ht.hotRankCur : '未上榜');
    }
  }

  /* 信息面板事件委托：chip 切换当前板块/跳板块页 / 日期回跳 K线 */
  function bindInfoEvents(box) {
    box.querySelectorAll('.sk-chip[data-plate]').forEach(c => {
      c.addEventListener('click', () => switchInfoPlate(c));
      const go = c.querySelector('.chip-go');
      if (go) go.addEventListener('click', (e) => { e.stopPropagation(); chipToSector(c); });
    });
    box.querySelectorAll('[data-date]').forEach(el => el.addEventListener('click', () => jumpToKline(el.dataset.date)));
    /* 核心卡趋势图：累计收益率 ↔ 当日收益率 切换 */
    box.querySelectorAll('.sk-mode-btn').forEach(b => b.addEventListener('click', () => {
      const cache = S.infoCache[S.code] || (S.infoCache[S.code] = {});
      cache._sparkMode = b.dataset.mode;
      box.querySelectorAll('.sk-mode-btn').forEach(x => {
        const on = x.dataset.mode === cache._sparkMode;
        x.style.background = on ? 'rgba(74,158,255,0.15)' : 'transparent';
        x.style.borderColor = on ? 'var(--accent-blue)' : 'var(--border-color)';
        x.style.color = on ? 'var(--text-primary)' : 'var(--text-secondary)';
      });
      paintInfoCharts(S.infoCache[S.code], S.code);
    }));
  }

  /* 点击板块/题材 chip：切换核心卡趋势图 + 行情对标为对应板块/题材 */
  async function switchInfoPlate(chip) {
    const code = S.code;
    const pcode = chip.dataset.plate, ptype = chip.dataset.ptype || 'industry', pname = chip.dataset.pname;
    if (!pcode || !code) return;
    const cache = S.infoCache[code] || (S.infoCache[code] = {});
    const sector = ((cache.sector || {}).data) || {};
    const panel = document.getElementById('skInfoPanel');
    if (!panel) return;
    /* 高亮点击的 chip，更新"当前板块"徽章文本 */
    panel.querySelectorAll('.sk-chip.active').forEach(x => x.classList.remove('active'));
    chip.classList.add('active');
    const curBadge = panel.querySelector('.sk-chip.cur-badge');
    if (curBadge) curBadge.innerHTML = `当前板块 ${esc(pname)}`;
    /* 找到缓存板块指标（行业 + 概念 top6），有则同步更新指标徽章区 */
    const sec = (sector.sectors || []).find(s => s.plateCode === pcode) || null;
    const badgeRow = panel.querySelector('.sk-badge-row');
    if (sec && badgeRow) {
      const up = v => v > 0 ? 'pos' : (v < 0 ? 'neg' : '');
      const sign = v => (v > 0 ? '+' : '') + (v != null ? v : '-');
      badgeRow.innerHTML =
        `<span class="sk-badge">市值权重<b class="${sec.weight >= 15 ? 'pos' : ''}">${(sec.weight || 0).toFixed(1)}%</b></span>` +
        `<span class="sk-badge">板块内涨幅<b>${sec.rank || '-'}/${sec.totalRank || '-'}</b></span>` +
        `<span class="sk-badge">龙头定位<b>${esc(sec.leader || '-')}</b></span>` +
        `<span class="sk-badge">涨跌贡献<b class="${up(sec.contrib)}">${sign(sec.contrib)}pp · 第${sec.contribRank || '-'}</b></span>` +
        `<span class="sk-badge">成交占比<b>${(sec.amtShare || 0).toFixed(1)}%</b></span>` +
        `<span class="sk-badge">资金占比<b>${sec.mfShare != null && sec.mfShare < 1000 ? sec.mfShare.toFixed(1) + '%' : '-'}</b></span>` +
        `<span class="sk-badge">当日状态<b class="${sec.state === '领涨' || sec.state === '逆势' ? 'pos' : 'neg'}">${esc(sec.state || '-')}</b></span>` +
        `<span class="sk-badge">RS1<b class="${up(sec.rs1)}">${sign(sec.rs1)}pp</b></span>` +
        `<span class="sk-badge">RS5<b class="${up(sec.rs5)}">${sign(sec.rs5)}pp</b></span>` +
        `<span class="sk-badge">RS20<b class="${up(sec.rs20)}">${sign(sec.rs20)}pp</b></span>` +
        `<span class="sk-badge">20日相关<b>${sec.corr20 != null ? sec.corr20 : '-'}</b></span>` +
        `<span class="sk-badge">趋势<b>${esc(sec.trend || '-')}</b></span>` +
        `<span class="sk-badge">个股位置<b>${esc(sec.stkPos || '-')}</b></span>`;
    }
    /* 趋势图：板块线切换为选中板块 */
    await initSparkVsIndex(code, { plateCode: pcode, plateType: ptype, plateName: pname });
    /* 行情对标：重拉对应板块占比 */
    try {
      const resp = await fetch(`${API}/stock/info?code=${encodeURIComponent(code)}&module=bench&type=${ptype}&plate=${encodeURIComponent(pcode)}`).then(r => r.ok ? r.json() : null).catch(() => null);
      if (resp && resp.data) {
        cache.bench = resp;
        const bd = resp.data;
        const ap = document.getElementById('skBenchAmtPct'), mp = document.getElementById('skBenchMvPct');
        if (ap) ap.textContent = bd.amtShare != null ? bd.amtShare + '%' : '—';
        if (mp) mp.textContent = bd.mvShare != null ? bd.mvShare + '%' : '—';
        /* 同步更新"个股成交额/板块合计/个股流通市值/板块合计"四个数值（限定在行情对标卡片内） */
        let bcard = null;
        panel.querySelectorAll('.sk-info-card').forEach(c => { if (!bcard && c.innerText.indexOf('行情对标') >= 0) bcard = c; });
        if (bcard) {
          const kvs = bcard.querySelectorAll('.sk-kv .v');
          const f = (v, u) => v != null ? v + u : '—';
          if (kvs.length >= 4) {
            kvs[0].textContent = f(bd.amt, '亿');
            kvs[1].textContent = f(bd.amtTotal, '亿');
            kvs[2].textContent = f(bd.circMv, '亿');
            kvs[3].textContent = f(bd.circTotal, '亿');
          }
        }
      }
    } catch (e) { /* 忽略 */ }
    paintInfoCharts(cache, code);
  }

  /* ================= 模拟交易（阶段一：本地账本 + 回放撮合） ================= */
  function simApi(path, opts) {
    return fetch(API + path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}))
      .then(r => r.json()).catch(() => null);
  }

  function shortCode(c) { return String(c || '').split('.')[0]; }

  function simToast(msg, bad) {
    const el = $('skSimEst');
    if (!el) return;
    el.textContent = msg || '';
    el.style.color = bad ? '#ff5c5c' : '#00c853';
    if (S.sim.toastT) clearTimeout(S.sim.toastT);
    S.sim.toastT = setTimeout(() => { simEst(); }, 5000);
  }

  function simSyncCode() {
    const c = $('skSimCode'), n = $('skSimName'), p = $('skSimPrice');
    if (c) c.value = S.code || '';
    if (n) n.textContent = S.name || shortCode(S.code);
    if (p && S.bars && S.bars.length) p.value = S.bars[S.bars.length - 1].close.toFixed(2);
    simEst();
  }

  function simLoadCfg() {
    simApi('/sim/config').then(c => {
      S.sim.cfg = c || { participation_rate: 0, slippage_bps: 2 };
      simRenderCfg();
    });
  }

  function simRenderCfg() {
    const cb = $('skSimPart'), v = $('skSimPartVal');
    const rate = (S.sim.cfg && S.sim.cfg.participation_rate) || 0;
    if (cb) cb.checked = rate > 0;
    if (v) v.textContent = rate > 0 ? ('单笔 ≤ ' + Math.round(rate * 100) + '% 成交量') : '关闭';
  }

  function simMaxQty() {
    const code = (($('skSimCode') || {}).value || '').trim().toUpperCase();
    if (S.sim.side === 'sell') {
      const pos = ((S.sim.acc && S.sim.acc.positions) || []).find(x => x.code === code);
      return pos ? (pos.avail_qty || 0) : 0;
    }
    const px = parseFloat(($('skSimPrice') || {}).value) || 0;
    const cash = (S.sim.acc && S.sim.acc.cash) || 0;
    if (px <= 0) return 0;
    return Math.floor(cash / px / 100) * 100;
  }

  function simEst() {
    const el = $('skSimEst');
    if (!el) return;
    const px = parseFloat(($('skSimPrice') || {}).value) || 0;
    const qty = parseInt(($('skSimQty') || {}).value) || 0;
    const amt = px * qty;
    el.style.color = '#7d8794';
    el.textContent = '预估金额 ' + (amt ? amt.toFixed(2) : '—') +
      ' · 可' + (S.sim.side === 'buy' ? '买' : '卖') + ' ' + simMaxQty() + ' 股';
  }

  async function simSubmit() {
    if (S.sim.busy) return;
    const code = (($('skSimCode') || {}).value || '').trim().toUpperCase();
    const price = parseFloat(($('skSimPrice') || {}).value) || 0;
    const qty = parseInt(($('skSimQty') || {}).value) || 0;
    if (!/^\d{6}\.(SH|SZ|BJ)$/.test(code)) { simToast('代码格式应为 600519.SH', true); return; }
    if (!qty) { simToast('请填写委托数量', true); return; }
    if (S.sim.otype === 'limit' && !price) { simToast('限价委托请填写价格', true); return; }
    S.sim.busy = true;
    const btn = $('skSimSubmit'); if (btn) btn.disabled = true;
    const r = await simApi('/sim/order?mode=' + simAcct(), {
      method: 'POST',
      body: JSON.stringify({
        code: code, name: ($('skSimName') || {}).textContent || '',
        side: S.sim.side, otype: S.sim.otype, price: price, qty: qty,
        asof: S.endDate || '', mode: simAcct()
      })
    });
    S.sim.busy = false;
    if (btn) btn.disabled = false;
    if (!r) { simToast('后端未响应，检查 5000 端口', true); return; }
    if (r.ok) {
      const f = (r.fills || [])[0];
      if (r.status === 'filled' && f) {
        simToast('成交 ' + f.qty + ' 股 @ ' + f.price + '（' + (r.asof || '') + '）');
      } else if (r.status === 'partial' && f) {
        simToast('部分成交 ' + f.qty + ' 股 @ ' + f.price + '，剩余挂单');
      } else {
        simToast('已挂单（' + (r.asof || '') + '），等下一交易日撮合');
      }
    } else {
      simToast(r.reason || '下单被拒', true);
    }
    simRefresh();
  }

  async function simReplayStep(asof) {
    if (!asof || !/^\d{4}-\d{2}-\d{2}$/.test(asof)) return;
    if (simAcct() === 'live') { simToast('实盘账户不支持历史回放，请切到回测账户', true); return; }
    const codes = [S.code].concat(((S.sim.acc && S.sim.acc.positions) || []).map(p => p.code));
    const r = await simApi('/sim/replay/step?mode=' + simAcct(), {
      method: 'POST', body: JSON.stringify({ asof: asof, codes: codes, mode: simAcct() })
    });
    if (r && r.ok) {
      const f = (r.fills || []).length, c = (r.conditions || []).length;
      if (c) {
        const KIND = { tp: '止盈', sl: '止损', trail: '回落' };
        const hit = (r.conditions || []).map(x => (KIND[x.kind] || x.kind) + ' ' + shortCode(x.code) + ' @' + x.price).join('，');
        simToast('回放 ' + asof + ' 触发：' + hit);
      } else if (f) {
        simToast('回放 ' + asof + '：撮合 ' + f + ' 笔委托');
      }
    }
    simRefresh();
  }

  /* 面板账户：显式选择后固定，未选择时跟随左侧列表 */
  function simListMode() { return (S.mode === 'plan_live') ? 'live' : 'backtest'; }
  function simAcct() { return S.sim.acctFixed ? S.sim.acct : simListMode(); }

  function simSyncAcctTabs() {
    const m = simAcct();
    S.sim.acct = m;
    const wrap = $('skSimAcct');
    if (wrap) wrap.querySelectorAll('span[data-m]').forEach(x => x.classList.toggle('on', x.dataset.m === m));
    const tip = $('skSimAcctTip');
    if (tip) tip.textContent = S.sim.acctFixed ? '标记跟着左侧列表' : ('跟随列表 · ' + (m === 'live' ? '实盘' : '回测'));
  }

  function simSwitchAcct(m) {
    if (m !== 'backtest' && m !== 'live') return;
    S.sim.acct = m; S.sim.acctFixed = true;
    S.sim.acc = null; S.sim.ptrades = []; S.sim.orders = []; S.sim.conds = [];
    const accEl = $('skSimAcc'); if (accEl) accEl.innerHTML = '';
    const posEl = $('skSimPos'); if (posEl) posEl.innerHTML = '<div class="sk-sim-tr" style="color:#7d8794"><span style="grid-column:1/6">加载中…</span></div>';
    const ordEl = $('skSimOrders'); if (ordEl) ordEl.innerHTML = '';
    const trEl = $('skSimTrades'); if (trEl) trEl.innerHTML = '';
    const cEl = $('skSimCondList'); if (cEl) cEl.innerHTML = '';
    simSyncAcctTabs();
    simToast('已切到' + (m === 'live' ? '实盘' : '回测') + '账户');
    simRefresh();
  }

  async function simRefresh() {
    if (S.sim.busy) return;
    // 面板数据走「面板账户」；K 线买卖标记仍由左侧列表模式决定，两者互不干扰
    if (!S.sim.acctFixed) S.sim.acct = simListMode();
    simSyncAcctTabs();
    const am = simAcct();          // 面板账本
    const lm = simListMode();      // 标记账本
    const reqs = [simApi('/sim/account?mode=' + am), simApi('/sim/orders?limit=30&mode=' + am),
      simApi('/sim/trades?limit=500&mode=' + am), simApi('/sim/cond?status=active&mode=' + am)];
    if (lm !== am) reqs.push(simApi('/sim/trades?limit=500&mode=' + lm));
    const res = await Promise.all(reqs);
    if (res[0]) S.sim.acc = res[0];
    if (res[1]) S.sim.orders = res[1].orders || [];
    if (res[2]) S.sim.ptrades = res[2].trades || [];
    if (res[3]) S.sim.conds = res[3].cond_orders || [];
    S.sim.trades = (lm === am) ? (S.sim.ptrades || []) : (res[4] ? (res[4].trades || []) : []);
    simRender();
    simRenderCond();
    drawSimMarks();
  }

  /* 模拟成交标记：DOM 层绝对定位（买红卖蓝）
   * klinecharts 的 simpleAnnotation 在本页把点画到 pane 顶部且 styles 颜色不生效，
   * 改用 chart.convertToPixel 把 (交易日, 成交价) 换算成容器像素，保证位置与文字都对。 */
  function drawSimMarks() {
    const el = $('stockChart');
    if (!el || !S.chart || !S.code) return;
    try {
      if (S.markIds && S.markIds.length) {
        S.markIds.forEach(id => { try { S.chart.removeOverlay(id); } catch (e) { } });
      }
      S.markIds = [];
      if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
      let layer = document.getElementById('skMarkLayer');
      if (!layer) {
        layer = document.createElement('div');
        layer.id = 'skMarkLayer';
        el.appendChild(layer);
      }
      layer.style.cssText = 'position:absolute;left:0;top:0;right:0;bottom:0;pointer-events:none;z-index:8';
      layer.innerHTML = '';
      const bars = S.bars || [];
      const barsTs = bars.map(b => b.timestamp);
      const tr = ((S.sim && S.sim.trades) || []).filter(t => t.code === S.code).slice(-60);
      const drawn = [];
      tr.forEach(t => {
        const t0 = new Date(t.asof + 'T00:00:00').getTime();
        /* 计划日可能落在非交易日（timed 顺延到下一交易日、停牌等），
           点位吸到「不晚于该日」的最近一根 K 线 */
        let ts = t0;
        if (barsTs.length && barsTs.indexOf(ts) < 0) {
          let best = null;
          for (let i = 0; i < barsTs.length; i++) { if (barsTs[i] <= t0) best = barsTs[i]; }
          if (best == null) return;
          ts = best;
        }
        let pt = null;
        try { pt = S.chart.convertToPixel({ timestamp: ts, value: Number(t.price) }); } catch (e) { pt = null; }
        if (!pt || !isFinite(pt.x) || !isFinite(pt.y)) return;
        const buy = t.side === 'buy';
        const color = buy ? '#f23645' : '#4a9eff';
        const dot = document.createElement('div');
        dot.style.cssText = 'position:absolute;width:9px;height:9px;border-radius:50%;left:' +
          (pt.x - 4.5) + 'px;top:' + (pt.y - 4.5) + 'px;background:' + color +
          ';border:1px solid rgba(255,255,255,.8);box-sizing:border-box';
        const lab = document.createElement('div');
        lab.textContent = (buy ? '买' : '卖') + (t.qty || '');
        /* 买标签在点上方、卖标签在点下方：同一价位附近买卖不会互相压住；
           靠右边缘时标签翻到点的左侧，避免被裁 */
        const lbLeft = (pt.x + 7 + 64 > el.clientWidth) ? (pt.x - 68) : (pt.x + 7);
        lab.style.cssText = 'position:absolute;left:' + lbLeft + 'px;top:' + (pt.y + (buy ? -17 : 3)) + 'px;' +
          'font-size:10px;line-height:14px;padding:0 4px;border-radius:2px;color:#fff;background:' + color +
          ';white-space:nowrap;font-weight:600';
        layer.appendChild(dot);
        layer.appendChild(lab);
        drawn.push({ t: t, x: Math.round(pt.x), y: Math.round(pt.y) });
      });
      try { window.__skMarks = drawn; } catch (e) { }
      // 文字明细
      let tip = document.getElementById('skSimMarkTip');
      if (!tip) {
        tip = document.createElement('div');
        tip.id = 'skSimMarkTip';
        tip.style.cssText = 'font-size:11px;color:#9aa0aa;margin-top:2px';
        const st = document.getElementById('skStatus');
        if (st && st.parentNode) st.parentNode.insertBefore(tip, st.nextSibling);
      }
      tip.textContent = drawn.length
        ? ('模拟成交：' + drawn.map(d => (d.t.asof || '').slice(5) + ' ' + (d.t.side === 'buy' ? '买' : '卖') +
            (d.t.qty || '') + '@' + d.t.price).join(' · '))
        : '';
    } catch (e) { }
  }

  function simRenderCond() {
    const el = $('skSimCondList');
    if (!el) return;
    const list = S.sim.conds || [];
    const cnt = $('skSimCondCnt');
    if (cnt) cnt.textContent = list.length ? '(' + list.length + ')' : '';
    const KIND = { tp: '止盈', sl: '止损', trail: '回落', fixed_price: '限价', timed: '定时' };
    let html = '<div class="sk-sim-tr head"><span>标的</span><span>类型</span><span>参数</span><span>触发价</span><span>操作</span></div>';
    if (!list.length) {
      html += '<div class="sk-sim-tr" style="color:#7d8794"><span style="grid-column:1/6">暂无条件单</span></div>';
    }
    list.forEach(c => {
      const pct = (c.value || 0) * 100;
      const armP = (c.arm_value || 0) * 100;
      const isArm = c.kind === 'trail' && armP > 0;
      const name = (KIND[c.kind] || c.kind) + (isArm ? ('↑' + armP.toFixed(1) + '%') : '');
      let param = pct.toFixed(1) + '%';
      if (c.kind === 'fixed_price') param = (+c.value).toFixed(2);
      if (c.kind === 'timed') param = c.due_date || '—';
      const trig = (isArm && !(+c.armed || 0)) ? '待激活' :
        (c.trigger_price ? Number(c.trigger_price).toFixed(2) : '—');
      html += '<div class="sk-sim-tr">' +
        '<span>' + shortCode(c.code) + '</span>' +
        '<span>' + name + '</span>' +
        '<span>' + param + '</span>' +
        '<span>' + trig + '</span>' +
        '<span class="act" data-cancel="' + c.id + '">撤</span>' +
        '</div>';
    });
    el.innerHTML = html;
    el.querySelectorAll('[data-cancel]').forEach(b => {
      b.onclick = function () { simCancelCond(+this.getAttribute('data-cancel')); };
    });
  }

  async function simCancelCond(id) {
    const r = await simApi('/sim/cond/cancel?mode=' + simAcct(), {
      method: 'POST', body: JSON.stringify({ cond_id: id, asof: S.endDate || '', mode: simAcct() })
    });
    if (r && r.ok === false && r.reason) simToast(r.reason, true);
    simRefresh();
  }

  async function simSetCond() {
    const code = (($('skSimCode') || {}).value || '').trim().toUpperCase();
    if (!/^\d{6}\.(SH|SZ|BJ)$/.test(code)) { simToast('代码格式应为 600519.SH', true); return; }
    const tp = parseFloat(($('skSimCtp') || {}).value) || 0;
    const sl = parseFloat(($('skSimCsl') || {}).value) || 0;
    const tr = parseFloat(($('skSimCtr') || {}).value) || 0;
    const arm = parseFloat(($('skSimArm') || {}).value) || 0;
    if (arm > 0 && !(tr > 0)) { simToast('“先涨 %”需同时填“回落 %”', true); return; }
    const items = [];
    if (tp > 0) items.push(['tp', tp]);
    if (sl > 0) items.push(['sl', sl]);
    if (tr > 0) items.push(['trail', tr, arm]);
    if (!items.length) { simToast('至少填一个百分比', true); return; }
    let okN = 0, msg = '';
    for (let i = 0; i < items.length; i++) {
      const r = await simApi('/sim/cond?mode=' + simAcct(), {
        method: 'POST',
        body: JSON.stringify({ code: code, kind: items[i][0], pct: items[i][1] / 100,
          arm_pct: (items[i][2] || 0) / 100, asof: S.endDate || '', mode: simAcct() })
      });
      if (r && r.ok) okN++;
      else if (r && r.reason) msg = r.reason;
      else if (!r) msg = '后端未响应';
    }
    simToast(okN ? ('已设置 ' + okN + ' 个条件单') : (msg || '设置失败'), !okN);
    if ($('skSimCtp')) $('skSimCtp').value = '';
    if ($('skSimCsl')) $('skSimCsl').value = '';
    if ($('skSimCtr')) $('skSimCtr').value = '';
    if ($('skSimArm')) $('skSimArm').value = '';
    simRefresh();
  }

  function simRender() {
    const acc = S.sim.acc;
    const modeEl = $('skSimMode');
    if (modeEl) {
      modeEl.textContent = S.endDate ? ('回放 ' + S.endDate) : '最新';
      modeEl.classList.toggle('live', !S.endDate);
    }
    if (acc) {
      const pnl = (acc.nav || 0) - (acc.initial_cash || 0);
      const cls = pnl > 0 ? 'up' : pnl < 0 ? 'down' : '';
      const accEl = $('skSimAcc');
      if (accEl) accEl.innerHTML =
        '<div><label>总资产</label><b>' + (acc.nav || 0).toFixed(2) + '</b></div>' +
        '<div><label>可用资金</label><b>' + (acc.cash || 0).toFixed(2) + '</b></div>' +
        '<div><label>持仓市值</label><b>' + (acc.market_value || 0).toFixed(2) + '</b></div>' +
        '<div><label>浮动盈亏</label><b class="' + cls + '">' + (pnl > 0 ? '+' : '') + pnl.toFixed(2) + '</b></div>' +
        '<div><label>总收益率</label><b class="' + cls + '">' + ((acc.total_return_pct || 0) > 0 ? '+' : '') + (acc.total_return_pct || 0).toFixed(2) + '%</b></div>' +
        '<div><label>冻结资金</label><b>' + (acc.frozen || 0).toFixed(2) + '</b></div>';
      // 持仓
      const list = acc.positions || [];
      const posEl = $('skSimPos');
      if (posEl) {
        let html = '<div class="sk-sim-tr head"><span>代码</span><span>持仓/可用</span><span>成本</span><span>现价</span><span>浮盈</span></div>';
        if (!list.length) html += '<div class="sk-sim-tr" style="color:#7d8794"><span style="grid-column:1/6">暂无持仓</span></div>';
        list.forEach(p => {
          const pc = p.pnl > 0 ? 'up' : p.pnl < 0 ? 'down' : '';
          html += '<div class="sk-sim-tr">' +
            '<span class="act" data-fill="' + esc(p.code) + '">' + shortCode(p.code) + '</span>' +
            '<span>' + p.qty + '/' + p.avail_qty + '</span>' +
            '<span>' + (p.cost || 0).toFixed(2) + '</span>' +
            '<span>' + (p.last_price || 0).toFixed(2) + '</span>' +
            '<span class="' + pc + '" title="成本 ' + (p.cost || 0).toFixed(2) + ' / 盈亏 ' + (p.pnl_pct || 0).toFixed(2) + '%">' +
            (p.pnl > 0 ? '+' : '') + (p.pnl || 0).toFixed(0) + '</span></div>';
        });
        posEl.innerHTML = html;
      }
      const pcnt = $('skSimPosCnt'); if (pcnt) pcnt.textContent = list.length ? '(' + list.length + ')' : '';
    }
    // 委托
    const ordEl = $('skSimOrders');
    if (ordEl) {
      const list = S.sim.orders || [];
      const stNames = { pending: '待成交', partial: '部分成交', filled: '已成交', cancelled: '已撤', rejected: '废单' };
      let html = '<div class="sk-sim-tr head"><span>代码</span><span>方向</span><span>价×量</span><span>状态</span><span>操作</span></div>';
      if (!list.length) html += '<div class="sk-sim-tr" style="color:#7d8794"><span style="grid-column:1/6">暂无委托</span></div>';
      list.slice(0, 25).forEach(o => {
        const active = (o.status === 'pending' || o.status === 'partial');
        html += '<div class="sk-sim-tr">' +
          '<span>' + shortCode(o.code) + '</span>' +
          '<span class="' + (o.side === 'buy' ? 'up' : 'down') + '">' + (o.side === 'buy' ? '买' : '卖') + '</span>' +
          '<span>' + (o.price ? Number(o.price).toFixed(2) : '市价') + '×' + o.qty + '</span>' +
          '<span title="' + esc(o.reason || '') + '">' + (stNames[o.status] || o.status) + '</span>' +
          '<span>' + (active ? '<span class="act" data-cancel="' + o.id + '">撤单</span>' : '—') + '</span></div>';
      });
      ordEl.innerHTML = html;
      const ocnt = $('skSimOrdCnt'); if (ocnt) ocnt.textContent = list.length ? '(' + list.length + ')' : '';
    }
    // 成交（面板账户口径；K 线标记另有 S.sim.trades 由左侧列表决定）
    const trEl = $('skSimTrades');
    if (trEl) {
      const list = S.sim.ptrades || [];
      let html = '<div class="sk-sim-tr head"><span>时间</span><span>代码</span><span>方向</span><span>价×量</span><span>费用</span></div>';
      if (!list.length) html += '<div class="sk-sim-tr" style="color:#7d8794"><span style="grid-column:1/6">暂无成交</span></div>';
      list.slice(0, 25).forEach(t => {
        html += '<div class="sk-sim-tr">' +
          '<span>' + esc(String(t.asof || '').slice(5)) + '</span>' +
          '<span>' + shortCode(t.code) + '</span>' +
          '<span class="' + (t.side === 'buy' ? 'up' : 'down') + '">' + (t.side === 'buy' ? '买' : '卖') + '</span>' +
          '<span>' + Number(t.price).toFixed(2) + '×' + t.qty + '</span>' +
          '<span>' + Number(t.fee || 0).toFixed(2) + '</span></div>';
      });
      trEl.innerHTML = html;
      const tcnt = $('skSimTrdCnt'); if (tcnt) tcnt.textContent = list.length ? '(' + list.length + ')' : '';
    }
    simEst();
  }

  function simOpen() {
    const d = $('skSimDock'); if (!d) return;
    d.classList.add('on');
    S.sim.on = true;
    if (!S.sim.acctFixed) S.sim.acct = simListMode();
    simSyncAcctTabs();
    simSyncCode();
    simRefresh();
    setTimeout(() => { if (S.chart) S.chart.resize(); }, 60);
    if (S.sim.timer) clearInterval(S.sim.timer);
    S.sim.timer = setInterval(() => { if (S.sim.on) simRefresh(); }, 4000);
  }

  function simClose() {
    const d = $('skSimDock'); if (d) d.classList.remove('on');
    S.sim.on = false;
    if (S.sim.timer) { clearInterval(S.sim.timer); S.sim.timer = null; }
    setTimeout(() => { if (S.chart) S.chart.resize(); }, 60);
  }

  function initSim() {
    const btn = $('skSimBtn'), dock = $('skSimDock'), close = $('skSimClose');
    if (!dock || !btn) return;
    btn.addEventListener('click', () => (S.sim.on ? simClose() : simOpen()));
    if (close) close.addEventListener('click', simClose);
    const acct = $('skSimAcct');
    if (acct) acct.addEventListener('click', e => {
      const t = e.target.closest('span[data-m]');
      if (t && t.dataset.m !== simAcct()) simSwitchAcct(t.dataset.m);
    });
    const condSet = $('skSimCondSet');
    if (condSet) condSet.addEventListener('click', simSetCond);
    const sideSeg = $('skSimSide'), otypeSeg = $('skSimOType');
    if (sideSeg) sideSeg.addEventListener('click', e => {
      const t = e.target.closest('[data-s]'); if (!t) return;
      S.sim.side = t.dataset.s;
      sideSeg.querySelectorAll('span').forEach(x => x.classList.toggle('on', x === t));
      const sb = $('skSimSubmit');
      if (sb) { sb.textContent = S.sim.side === 'buy' ? '买入' : '卖出'; sb.classList.toggle('sell', S.sim.side === 'sell'); }
      simEst();
    });
    if (otypeSeg) otypeSeg.addEventListener('click', e => {
      const t = e.target.closest('[data-t]'); if (!t) return;
      S.sim.otype = t.dataset.t;
      otypeSeg.querySelectorAll('span').forEach(x => x.classList.toggle('on', x === t));
      simEst();
    });
    const qb = $('skSimQbtns');
    if (qb) qb.addEventListener('click', e => {
      const t = e.target.closest('[data-q]'); if (!t) return;
      const qEl = $('skSimQty'); if (!qEl) return;
      const maxQ = simMaxQty();
      if (t.dataset.q === '100') qEl.value = 100;
      else if (t.dataset.q === 'max') qEl.value = maxQ;
      else if (t.dataset.q === 'q1') qEl.value = Math.floor(maxQ / 4 / 100) * 100;
      else if (t.dataset.q === 'q2') qEl.value = Math.floor(maxQ / 2 / 100) * 100;
      simEst();
    });
    ['skSimPrice', 'skSimQty'].forEach(id => { const el = $(id); if (el) el.addEventListener('input', simEst); });
    const sub = $('skSimSubmit'); if (sub) sub.addEventListener('click', simSubmit);
    const part = $('skSimPart');
    if (part) part.addEventListener('change', () => {
      simApi('/sim/config', { method: 'POST', body: JSON.stringify({ participation_rate: part.checked ? 0.2 : 0 }) })
        .then(c => { if (c) S.sim.cfg = c; simRenderCfg(); });
    });
    const reset = $('skSimReset');
    if (reset) reset.addEventListener('click', () => {
      const md = simAcct();
      if (!confirm('重置「' + (md === 'live' ? '实盘' : '回测') + '」账户：清空该账本的持仓/委托/成交，恢复初始资金？')) return;
      simApi('/sim/reset?mode=' + md, { method: 'POST', body: JSON.stringify({ mode: md }) })
        .then(() => { simToast((md === 'live' ? '实盘' : '回测') + '账户已重置'); simRefresh(); });
    });
    const posEl = $('skSimPos');
    if (posEl) posEl.addEventListener('click', e => {
      const c = e.target.closest('[data-fill]'); if (!c) return;
      if (c.dataset.fill) { loadStock(c.dataset.fill); simSyncCode(); }
    });
    const ordEl = $('skSimOrders');
    if (ordEl) ordEl.addEventListener('click', e => {
      const c = e.target.closest('[data-cancel]'); if (!c) return;
      simApi('/sim/cancel?mode=' + simAcct(), { method: 'POST', body: JSON.stringify({ order_id: parseInt(c.dataset.cancel), asof: S.endDate || '', mode: simAcct() }) })
        .then(r => { simToast(r && r.ok ? '已撤单' : ((r && r.reason) || '撤单失败'), !(r && r.ok)); simRefresh(); });
    });
    const kd = $('skKlineDate');
    if (kd) kd.addEventListener('change', () => { if (S.sim.on) simReplayStep(S.endDate || ''); });
    simLoadCfg();
  }

  window.StockKline = { init: ensureKcActive, loadStock: loadStock };
  if (typeof App !== 'undefined') {
    App.initStockKlinePage = function () { if (window.StockKline) window.StockKline.init(); };
  }
  // 页面若已处于个股行情路由（hash 直达场景），立即接管
  if (typeof App !== 'undefined' && App.state && App.state.currentRoute === 'stock-kline') {
    ensureKcActive();
  }
})();
