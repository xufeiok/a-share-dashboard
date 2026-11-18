# 实盘批次计划（JSON）

系统只有两类业务 JSON：**回测结果** 与 **实盘计划**，两者模板不同、目录分开。

```
plans/
├── live/                        实盘计划 → 写 sim_trading_live.db（多实盘各自 live_accts/<id>.db）
│   ├── _template_live.json      多股票实盘计划模板（enabled=false）
│   └── plan_live_YYYYMMDD_*.json  真实计划
└── backtest_results/            回测结果（回测模式唯一输入目录）
    ├── template_backtest.json   多股票回测结果模板
    └── _pinned.json             页面「暂存指标」累计记录

trade_inbox/                     定时实盘收件箱（运行时生成，不入库）
```

## 命名规范

| 类型 | 格式 | 例子 |
|---|---|---|
| 实盘建仓 | `plan_live_YYYYMMDD_名称.json` | `plan_live_20261116_银行建仓.json` |
| 实盘调仓 | `plan_live_YYYYMMDD_名称_调仓.json` | `plan_live_20261201_减仓.json` |
| 回测结果 | `策略_YYYYMMDD_描述.json` | `ma_cross_20261118_三票.json` |

文件名只用中文、数字、下划线，避免空格和括号；不要以 `_` 开头（`_` 是系统内部文件：模板、`_pinned.json`、`_upload_*`）。

## 怎么执行

1. 打开看板 →「交易持仓」
2. **实盘**：填写计划路径（如 `plans/live/plan_live_20261120_建仓.json`）→「校验」→「执行」；也可在卡片里上传，或丢进 `trade_inbox/` 由定时实盘扫描
3. **回测**：把回测结果 JSON 放进 `plans/backtest_results/`，在下拉里选择即可；不用选模式，页面本身就是回测视图

实盘只更新实盘账本与实盘列表的 K 线标记；回测只读取回测结果文件。**同一只股票同时出现在两边也不会串图。**

定时实盘：`watch_dir` 指向 `trade_inbox/` 时，丢文件进去自动执行；也可以直接指定 `plans/live/xxx.json` 按 once / daily / monthly_nth_weekday 定时触发。

## 结构速查（实盘）

完整多股票示例见 `plans/live/_template_live.json`，最小结构如下：

```json
{
  "version": "1.1",
  "mode": "live",
  "name": "示例计划",
  "defaults": { "qty": { "type": "shares", "value": 100 } },
  "batches": [
    {
      "id": "b1",
      "name": "建仓",
      "enabled": true,
      "schedule": { "type": "once", "date": "2026-11-20", "time": "09:31" },
      "items": [
        {
          "code": "600519.SH",
          "name": "贵州茅台",
          "side": "buy",
          "qty": { "type": "shares", "value": 100 },
          "price": { "type": "market" },
          "exits": [
            { "seq": 1, "kind": "sl", "value": 0.05, "qty": { "type": "all" } },
            { "seq": 2, "kind": "trail", "value": 0.03, "arm": 0.10, "qty": { "type": "pct", "value": 0.5 } },
            { "seq": 3, "kind": "trail", "value": 0.05, "arm": 0.20, "qty": { "type": "all" } },
            { "seq": 4, "kind": "timed", "due": "2027-01-29", "qty": { "type": "all" } }
          ]
        }
      ]
    }
  ]
}
```

### schedule.type

| 值 | 含义 | 必填 |
|---|---|---|
| `once` | 指定日期时间触发一次 | `date`、`time` |
| `daily` | 每交易日固定时间 | `time`、可选 `weekdays`（1=周一） |
| `monthly_nth_weekday` | 每月第 N 个周 X | `nth`、`weekday`、`time`、可选 `date_from/date_to` |

### qty.type

| 值 | 含义 | 适用 |
|---|---|---|
| `shares` | 直接指定股数 | 买 / 卖 |
| `amount` | 指定金额，按价格折算股数 | 买 |
| `pct_nav` | 占账户净值的比例（0.2 或 20 都按 20% 理解） | 买 |
| `pct` | 占当前可用持仓的比例 | 卖 / exits |
| `all` | 全部可用持仓 | 卖 / exits |

### price.type

| 值 | 含义 |
|---|---|
| `market` | 市价（按下一根开盘价撮合） |
| `limit` | 限价（`value` 填价格，超出涨跌停会被拒单） |

### exits.kind

| kind | 含义 | 参数 |
|---|---|---|
| `sl` | 止损：跌破买入价的比例（0.05 = -5%） | `value` |
| `tp` | 固定止盈：涨到买入价的比例（0.10 = +10%） | `value` |
| `trail` | 移动止盈：先涨到 `arm` 激活，之后从最高价回落 `value` 卖出 | `value`、`arm` |
| `fixed_price` | 触价卖出 | `value`（价格） |
| `timed` | 到期卖出（到期日之后第一个交易日开盘） | `due`（YYYY-MM-DD） |

`exits` 按 `seq` 从小到大判断，每条的 `qty` 决定卖出多少当前持仓；未写默认全部。
