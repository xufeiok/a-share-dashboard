# indicators 指标库说明

本目录是 A股看板系统「个股行情页」的**指标算法源**，一指标一文件，新增/修改指标只需在此新建/编辑对应 `.py` 文件。

## 指标标准来源（2026-10 重构）

| 指标 | 算法标准 | 参数 |
|---|---|---|
| MA | backtrader 官方 SMA（`indicators/sma.py`） | 5 / 20 / 60 |
| MACD | backtrader 官方 MACD（`indicators/macd.py`） | fast=12, slow=26, signal=9；DIF=EMA12-EMA26，DEA=EMA9(DIF)，HIST=DIF-DEA |
| RSI | backtrader 官方 RSI（`indicators/rsi.py`） | period=14；UpDay/DownDay + Wilder 平滑（MovAv.Smoothed），70/30 阈值线 |
| KDJ | backtrader 官方 Stochastic（`indicators/stochastic.py`） | period=9, signal=3；K=SMA(RSV,3)，D=SMA(K,3)，J=3K-2D（J 为行情软件惯例） |
| SuperTrend | my_bt_indicator `SuperTrend.py` | period=10, multiplier=3.0 |
| VIDYA | my_bt_indicator `VolumeDynamicVIDYA.py` | length=10, momentum=20, atr=200 |
| UMA | my_bt_indicator `uma.py` | EMA 20/50/100 |
| OBV | my_bt_indicator `x_obv.py` | MA20 叠加 |
| XTR | my_bt_indicator `x_xtr.py` | period=14, band=20, mult=2.0 |
| BB评分 | my_bt_indicator `bb_trend_score.py` | bb_period=50, dev=2.0, roc=5 |

> 官方 backtrader 1.9.74.123 源码位置：
> `E:\Quant\backtrader_superplot\backtrader_superplot\backtrader-1.9.74.123\backtrader-1.9.74.123\backtrader\indicators\`
> 本目录已内置官方标准算法的核心实现（`backtrader_std_*.py` 后缀），供对照。

## 前端显示标准（参考 SuperPlot / backtrader plotlines）

| 指标 | 显示约定 |
|---|---|
| MACD | HIST 柱状（红涨绿跌），DIF 实线，DEA 虚线（官方 signal `ls='--'`），零轴线 |
| RSI | 主线上加 70/30 阈值虚线（官方 `plotyhlines=[70,30]`） |
| KDJ | K 实线、D 虚线（官方 `percD ls='--'`）、J 细线 |

## 如何新增指标（KLineChart + backtrader 桥技术路径）

后端链路：本目录 `.py` 算法 → `bt_indicator_bridge.calc_bt_indicators(df, params)`（桥挂载全部指标类）→ `/api/stock/indicators` 透出。

1. 在本目录新建 `xxx.py` 实现指标算法（或复用 `my_bt_indicator` 包内的类）
2. 在 `bt_indicator_bridge.py` 的 `calc_bt_indicators()` 中挂载（params 键名 + 默认值在此定义）
3. 前端 `vendor/klinecharts/stock-kline.js`：
   - 指标注册区 `klinecharts.registerIndicator({ name, calcParams, figures, calc })` —— calc 里读 `getInd().xxx` 后端数据；**多 figure 指标必须用 `styles: () => ({ color })` 函数取色、calc 只 set 有值的 key**（KLineChart v10 两个坑，违反即整条不渲染）
   - `MAIN_IND`/`SUB_IND` 加菜单映射、`MAIN_PARAMS`/`SUB_PARAMS` 加默认参数
   - `PARAM_MAP` 加「前端 calcParams 顺序 ↔ 后端参数键名」映射（设置面板保存时按此转后端键名请求重算）
   - `refreshCustomIndicators` 的主图/副图重建列表加入新指标名
   - `computeLatestVals` 信息栏加读数（读 `ind.xxx` 后端输出）
   - `index.html` 主图/副图下拉面板加 checkbox
4. 同名注册可覆盖 KLineChart 内置指标（如 MACD/RSI/KDJ/OBV 现为 bt 版），菜单名无需改动

## 前后端参数键名对照（PARAM_MAP，必须与 bt_indicator_bridge 一致）

| 指标 | 前端 calcParams 顺序 | 后端参数键名 |
|---|---|---|
| ST | period, mult | st_period, st_mult |
| VIDYA | length, momentum, atr, sma, band | vidya_len, vidya_mom, vidya_atr, vidya_sma, vidya_band |
| UMA | periods(数组) | ma_periods（逗号串） |
| X_XTR | period, ma, band, mult | xtr_period, xtr_ma, xtr_band, xtr_mult |
| BB评分 | period, dev, roc, acc, roll, w1, w2, w3 | bb_period, bb_dev, roc_period, acc_period, roll_window, w_nbbw, w_roc, w_acc |
| EnhancedMACD | fast, slow, signal | macd_fast, macd_slow, macd_signal |
| X_RSI | length, ma_length | rsi_length, rsi_ma_length |
| X_KDJ | period, signal | kdj_period, kdj_signal |
| X_OBV | ma | obv_ma |

> 页面指标名与算法源一致：EnhancedMACD ← EnhancedMACD.py，X_KDJ ← x_kdj.py，X_RSI ← x_rsi.py，X_OBV ← x_obv.py，X_XTR ← x_xtr.py，SuperTrend ← SuperTrend.py，动态VIDYA ← VolumeDynamicVIDYA.py，UMA ← uma.py，BB评分 ← bb_trend_score.py。信息栏数值颜色与对应图线颜色一致（DIF 蓝/DEA 黄/HIST 红涨绿跌、K 蓝/D 黄/J 灰、RSI 紫/黄、UMA 蓝绿灰等）。

## 文件清单

- `SuperTrend.py` / `VolumeDynamicVIDYA.py` / `uma.py` / `x_obv.py` / `x_xtr.py` / `x_kdj.py` / `x_rsi.py` / `EnhancedMACD.py` / `bb_trend_score.py`：自研/移植指标（来自 my_bt_indicator）
- `__init__.py`：包标记
