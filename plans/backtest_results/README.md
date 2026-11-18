# backtest_results — 回测结果 JSON 目录

交易持仓页「回测」标签会自动扫描本目录（`*.json`，忽略下划线开头的文件），
文件名出现在下拉列表里，选中即渲染指标卡、权益曲线、逐笔成交记录。

回测不再由看板内部撮合，而是直接读取 backtrader 跑完导出的结果文件。

**多股票完整模板**：`template_backtest.json`（3 只标的、6 笔委托、完整指标块），复制改名即可使用。

## 文件来源

- backtrader + SuperPlotView 导出的结果 JSON（参考 `E:\Quant\backtrader_superplot\backtrader_superplot\SuperPlotView\goodbacktest\`）
- 也可以由任意回测框架按下面的字段导出

## 必需字段

| 字段 | 说明 |
| --- | --- |
| `metrics_data` | 指标对象，见下表 |
| `portfolio_data.data[]` | `{datetime, value}`，账户权益曲线 |
| `orders_data.orders[]` | `{datetime, type: buy/sell, code, price, size, value, comm, pos_size}` 逐笔委托 |
| `trades_data.trades[]` | `{code, close_datetime, size, price, direction, return_pct, commission, pnl}` 平仓明细（可选） |
| `strategy_data` | `{name, timeframe, description, code, data_feeds[]}` 策略元信息（多标的用 data_feeds 列出） |
| `ohlcv_data.data[]` | `{datetime, open, high, low, close, volume}`，基准标的的日线，用于展示起止区间 |
| `position_data.data[]` | `{datetime, value}` 持仓市值（可选） |
| `analysis_data` | `{tradeanalyzer, drawdown, sharpe, returns, sqn}` 分析明细（可选） |

**多股票关键**：`orders_data.orders[]` 每笔必须带 `code`（如 `600519.SH`）。后端按 `code` 过滤买卖点，同一文件里不同标的会各自画到对应 K 线；缺 `code` 时只能回退顶层 `code`（单标的）。`strategy_data.code` 存的是策略源码，不是标的代码，不要拿来当股票代码用。

`metrics_data` 单位约定（与 SuperPlotView 一致）：

- `total_roi`、`annual_roi`、`volatility`：小数（0.4117 = 41.17%）
- `max_drawdown`、`win_rate`：已是百分数值（46.0 = 46%）
- `sharpe_ratio`、`sortino_ratio`、`calmar_ratio`、`profit_factor`、`sqn`、`total_trades`、`avg_pl_ratio`、`total_pnl`

## 暂存对比

页面右上「暂存指标」会把当前文件的指标写入 `_pinned.json`，
在「暂存回测指标」表格里累计，方便横向比较不同策略/参数。

## 当前文件

- `template_backtest.json`：多股票格式模板与字段说明（会被下拉列表列出，可直接选来看效果）
- `_pinned.json`：页面「暂存指标」累计的对比记录
- 自己的回测结果：命名如 `ma_cross_20261118_三票.json`，放入本目录后刷新下拉即出现

## 展示区间

回测文件的成交日期要落在个股 K 线可见区间内才画标记。个股页「回测」模式默认取 **600 根日线**（约 2.4 年）；
若成交日更早，页面底部会提示"有 N 笔成交早于当前K线区间"，用截止日或更长周期查看。

## 不入库

`_pinned.json` 与回测 JSON 体积较大，默认不提交到 git（见 `.gitignore`）。
