# 批次买卖计划（JSON）

所有"一次买/卖多只股票、并按条件分批卖出"的脚本都放在这里，与代码分离。

```
plans/
├── live/          实盘计划 → 写 sim_trading_live.db
├── backtest/      回测计划 → 写 sim_trading.db
└── templates/     模板与示例（不要直接改，复制一份出来用）
```

## 命名规范

| 类型 | 格式 | 例子 |
|---|---|---|
| 实盘建仓 | `plan_live_YYYYMMDD_名称.json` | `plan_live_20261116_银行建仓.json` |
| 实盘调仓 | `plan_live_YYYYMMDD_名称_调仓.json` | `plan_live_20261201_减仓.json` |
| 回测 | `plan_bt_YYYYMMDD_名称.json` | `plan_bt_20261118_移动止盈.json` |

文件名只用中文、数字、下划线，避免空格和括号。

## 怎么执行

1. 打开看板 →「交易持仓」
2. 选模式：**回测** 或 **实盘**
3. 填入相对项目根的路径，例如 `plans/backtest/plan_bt_20261118_移动止盈.json`
4. 点「校验」看结构是否通过，再点「执行」

回测模式只更新回测账本与回测列表的 K 线标记；实盘模式只更新实盘账本与实盘列表。
**同一只股票同时存在于两个计划中也不会串图。**

定时实盘（「定时实盘」卡片）：`watch_dir` 指向 `trade_inbox/`，把计划文件丢进去即自动执行；
也可以直接在卡片里指定 `plans/live/xxx.json` 按 daily / weekly / once 定时触发。

## 结构速查

```json
{
  "version": "1.0",
  "defaults": { "qty": { "type": "shares", "value": 100 } },
  "batches": [
    {
      "id": "b1",
      "enabled": true,
      "schedule": { "type": "once", "date": "2026-11-18", "time": "09:31" },
      "items": [
        {
          "code": "600519.SH",
          "side": "buy",
          "qty": { "type": "shares", "value": 100 },
          "price": { "type": "market" },
          "exits": [
            { "seq": 1, "kind": "sl", "value": 0.05, "qty": { "type": "pct", "value": 1.0 } }
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
| `daily` | 每交易日固定时间 | `time`、可选 `weekdays` |
| `monthly_nth_weekday` | 每月第 N 个周 X | `nth`、`weekday`、`time`、可选 `date_from/date_to` |

### qty.type

`shares`（股数）、`amount`（金额）、`pct_nav`（占净值比例）、`all`（全部持仓）、`pct`（占当前持仓比例，用于 exits）

### price.type

`market`（市价）、`close`（收盘价）、`pct_of_ref`（参考价上浮/下浮比例）

### exits.kind

| kind | 含义 | 参数 |
|---|---|---|
| `sl` | 止损：跌破买入价的比例 | `value` |
| `tp` | 固定止盈 | `value` |
| `trail` | 移动止盈：先涨到 `arm` 激活，之后从最高价回落 `value` 卖出 | `value`、`arm` |
| `fixed_price` | 触价卖出 | `value`（价格） |
| `timed` | 到期卖出（到期日之后第一个交易日） | `due` |

`exits` 按 `seq` 顺序判断，每条的 `qty` 决定卖出多少当前持仓；未写默认全部。
