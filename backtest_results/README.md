# backtest_results — 回测结果 JSON 目录

交易持仓页「回测」标签会自动扫描本目录（`*.json`，忽略下划线开头的文件），
文件名出现在下拉列表里，选中即渲染指标卡、权益曲线、逐笔成交记录。

回测不再由看板内部撮合，而是直接读取 backtrader 跑完导出的结果文件。

## 文件来源

- backtrader + SuperPlotView 导出的结果 JSON（参考 `E:\Quant\backtrader_superplot\backtrader_superplot\SuperPlotView\goodbacktest\`）
- 也可以由任意回测框架按下面的字段导出

## 必需字段

| 字段 | 说明 |
| --- | --- |
| `metrics_data` | 指标对象，见下表 |
| `portfolio_data.data[]` | `{datetime, value}`，账户权益曲线 |
| `orders_data.orders[]` | `{datetime, type: buy/sell, price, size, value, comm, pos_size}` 逐笔成交 |
| `trades_data.trades[]` | `{close_datetime, size, price, direction, return_pct, commission, pnl}` 平仓明细（可选） |
| `strategy_data` | `{name, timeframe, description, code}` 策略元信息 |
| `code`（推荐） | 形如 `600519.SH`。填了这个字段，个股 K 线页才会把该文件的买卖点画到对应标的图上 |
| `ohlcv_data.data[]` | `{datetime, open, high, low, close, volume}`，用于展示起止区间（可选） |

`metrics_data` 单位约定（与 SuperPlotView 一致）：

- `total_roi`、`annual_roi`、`volatility`：小数（0.4117 = 41.17%）
- `max_drawdown`、`win_rate`：已是百分数值（46.0 = 46%）
- `sharpe_ratio`、`sortino_ratio`、`calmar_ratio`、`profit_factor`、`sqn`、`total_trades`、`avg_pl_ratio`、`total_pnl`

## 暂存对比

页面右上「暂存指标」会把当前文件的指标写入 `_pinned.json`，
在「暂存回测指标」表格里累计，方便横向比较不同策略/参数。

## 当前样例

目录里的 `test_ehmacd_atr_Strategy_1768639543.json` 来自 SuperPlotView 自带的
BTC 4h 回测，仅用于验证页面展示；它没有 `code` 字段，所以个股 K 线上不会出现买卖点。
放入自己的 A 股回测结果（顶层带 `code`，如 `"code": "600519.SH"`）后即可在 K 线标注。

## 不入库

`_pinned.json` 与回测 JSON 体积较大，默认不提交到 git（见 `.gitignore`）。
