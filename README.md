# A股市场看板系统

本地运行的 A 股看板：Flask 数据服务 + 单页前端（`index.html`）+ 可选 ClickHouse 历史库 + 模拟交易账本（SQLite）。

## 功能

| 页面 | 内容 |
|---|---|
| 宏观环境 | 全球权益、中美利率、美债名义/实际收益率、汇率、商品、宏观打分 |
| 市场情绪 | 大盘指数、涨跌家数、涨跌停、ADL 腾落、成交与分布 |
| 板块行业 | 同花顺行业/概念热力图、涨幅矩阵、K 线下钻 |
| 个股行情 | 全市场排行、个股 K 线（含批次计划买卖标记） |
| 交易持仓 | 回测 + 多实盘账户（每个实盘独立账本），批次计划、条件单、定时实盘 |
| 因子透视 | 因子分组、IC、分层收益 |

## 环境

- Python 3.8+
- Windows / macOS / Linux 均可运行（前端是纯静态页）
- 可选：ClickHouse（行业/概念历史数据、股票名称）

## 本地运行

```bash
pip install -r requirements.txt
```

复制配置模板并填写本地值：

```bash
cp config_local.example.json config_local.json
```

`config_local.json` 字段：

| 字段 | 说明 |
|---|---|
| `ch_url` | ClickHouse HTTP 地址，如 `http://192.168.10.102:18123/`；没有可留空 |
| `ch_user` / `ch_password` | ClickHouse 账号 |
| `ch_db` | 数据库名，默认 `quant_db` |
| `tushare_token` | Tushare Pro token；留空则依赖 Tushare 的指标不可用 |

环境变量 `CH_URL`、`CH_USER`、`CH_PASSWORD`、`CH_DB`、`TUSHARE_TOKEN` 优先级高于配置文件。

启动：

```bash
python server.py
```

浏览器打开 `index.html` 即可（默认连 `http://127.0.0.1:5000/api`）。

## 前端 API 地址

页面顶部支持三种方式，优先级从高到低：

1. URL 参数：`index.html?api=https://your-domain.com`
2. 本地记住的地址（浏览器 localStorage，设置一次后自动沿用）
3. 默认 `http://127.0.0.1:5000/api`

清除记住的地址：访问 `index.html?api=`（空值）不会清除，可在浏览器控制台执行
`localStorage.removeItem('AK_API_BASE')`。

## 部署到 GitHub Pages（只有界面）

1. 推送到 GitHub 后，仓库 **Settings → Pages → Source: Deploy from a branch**，选择 `main` 和 `/ (root)`。
2. 访问 `https://<用户名>.github.io/<仓库名>/`。

注意：Pages 只托管静态文件，**不会运行 Flask**。页面能打开，但所有接口都会失败，
需要配合下一节的公网后端，用 `?api=` 指过去。

## 让外网/其他电脑真正可用

### 方案 A：Cloudflare Tunnel（推荐，零成本）

本机继续跑 `server.py`，用隧道把它暴露成公网 HTTPS 地址：

```bash
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://127.0.0.1:5000
```

命令会输出一个 `https://xxxx.trycloudflare.com` 地址。打开看板时：

```
index.html?api=https://xxxx.trycloudflare.com
```

地址会被记住，之后直接打开 `index.html` 即可。缺点：电脑必须开机、隧道进程必须运行；
临时域名每次重启会变（可绑定自有域名做固定隧道）。

### 方案 B：云服务器

把代码部署到有公网 IP 的服务器，安装 Python 依赖并运行 `server.py`（建议 gunicorn + Nginx）。
ClickHouse 需要公网可访问，或在服务器上另建一份数据；SQLite 账本会随服务器本地文件保存。

### 方案 C：仅静态展示

只推 Pages，页面结构可看，数据为空。适合演示 UI，不适合日常使用。

## 安全说明

以下内容已在 `.gitignore` 中排除，请勿提交：

- `config_local.json`（含 ClickHouse 账号、Tushare token）
- `*.db`（模拟交易账本，含持仓与成交记录）
- `trade_inbox/`（上传的选股文件）
- 日志与 `__pycache__`

公开仓库前建议再执行一次：

```bash
git status --short
git grep -n -i "token\|password\|192.168"
```

确认没有残留密钥。

## 目录

```
index.html                 前端单页（含全部页面与图表）
server.py                  Flask 数据服务
sim_store.py               模拟交易存储层（SQLite，回测/实盘双库）
sim_broker.py              撮合与条件单执行
sim_plan.py                批次计划解析
sim_auto.py                定时实盘调度
stock_indicators.py        技术指标
bt_indicator_bridge.py     Backtrader 指标桥
indicators/                指标实现
vendor/                    ECharts / KLineCharts 本地库
plans/                     批次买卖计划（JSON）
  ├── live/                实盘计划（默认写入 sim_trading_live.db，多实盘各自 live_accts/<id>.db）
  ├── backtest/            回测计划（写入 sim_trading.db）
  └── templates/           结构模板与完整示例
trade_inbox/               定时实盘收件箱（运行时生成，不入库）
Dockerfile / docker-compose.yml / DEPLOY_FNOS.md   飞牛OS 部署
```

## 批次计划怎么放

- 实盘：`plans/live/plan_live_YYYYMMDD_名称.json`
- 回测：`plans/backtest/plan_bt_YYYYMMDD_名称.json`
- 先复制 `plans/templates/quant_batch_template.json` 改，字段说明见 `plans/README.md`

前端「交易持仓」页选择模式后填写**相对项目根的路径**，两种模式各记各的账、各画各的买卖标记，互不干扰。
