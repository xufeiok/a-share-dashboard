# 飞牛OS（fnOS）部署说明

目标：把看板跑在飞牛OS 上（与 ClickHouse 同一台机器），局域网用 `http://<飞牛IP>:5000` 访问，
外网用固定域名访问。

## 架构

```
浏览器 ──HTTPS──> Cloudflare ──隧道──> 飞牛OS:5000（Flask，同时托管 index.html）
                                          │
                                          └──> 本机 ClickHouse 127.0.0.1:18123 / 局域网 192.168.10.102:18123
```

Flask 现在同时提供页面和 API，所以外网只需要暴露一个地址，不用再配 `?api=`。

## 一、准备文件

在飞牛OS 的 Docker 目录建一个项目文件夹，例如：

```
/vol1/1000/docker/akanban/
```

把本项目全部文件放进去（可以用飞牛的文件管理器上传 zip 解压，或 `git clone`）。

在项目根目录新建 `.env`（不要提交到仓库）：

```env
CH_URL=http://192.168.10.102:18123/
CH_USER=reader
CH_PASSWORD=123
CH_DB=quant_db
TUSHARE_TOKEN=你的token
PORT=5000
```

同目录放 `Dockerfile`、`docker-compose.yml`（仓库里已带）。

## 二、启动

飞牛OS 应用中心安装 Docker（若未安装），然后在项目目录执行：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f --tail=100
```

看到 `A股看板数据 API 服务 ... 端口:5000` 即启动成功。

局域网访问：

```
http://<飞牛OS的局域网IP>:5000
```

页面与数据应同时可用；若页面能开但板块数据为空，检查 `.env` 里的 `CH_URL` 是否能在容器内访问：

```bash
docker compose exec akanban python -c "import urllib.request;print(urllib.request.urlopen('http://192.168.10.102:18123/',timeout=5).status)"
```

## 三、外网访问

### 方式 A：Cloudflare Tunnel（推荐，固定域名 + 免费 HTTPS）

前提：有一个域名，且已托管到 Cloudflare（免费套餐即可）。

1. 登录 Cloudflare → **Zero Trust → Networks → Tunnels → Create a tunnel**（选 Cloudflared）。
2. 复制生成的 Tunnel Token。
3. 在 `.env` 追加：

```env
TUNNEL_TOKEN=你的token
```

4. `docker-compose.yml` 里已预留 `tunnel` 服务，取消注释后重启：

```bash
docker compose up -d
```

5. 在 Tunnel 的 **Public Hostname** 里配置：
   - Subdomain: `akanban`
   - Domain: 你的域名
   - Service: `http://akanban:5000`（compose 内部服务名）

之后任何网络访问 `https://akanban.你的域名` 即可，地址固定、带 HTTPS。

### 方式 B：临时隧道（没有域名时先验证）

在飞牛OS 上执行：

```bash
docker run --rm cloudflare/cloudflared:latest tunnel --url http://host.docker.internal:5000
```

会输出一个 `https://xxxx.trycloudflare.com` 地址，可直接访问；进程停止即失效，地址也会变。

### 方式 C：路由器端口映射（需要有公网 IP）

路由器把外网 `5000` 映射到飞牛OS 的 `5000`，再用 `http://公网IP:5000` 访问。
家宽多数没有公网 IPv4，且运营商常封 80/443，成功率高的是方式 A。

### 方式 D：飞牛OS 自带远程访问

飞牛OS 若已开启自带的远程访问/DDNS，也可以把内部 `5000` 端口通过它的反代暴露出来；
具体入口在 fnOS 的「系统设置 → 远程访问」。有域名时同样建议套 HTTPS。

## 四、数据持久化

`docker-compose.yml` 把项目目录挂载到容器 `/app`，因此：

- `sim_trading.db`、`sim_trading_live.db` 保存在宿主机项目目录，重建容器不丢
- 修改 `index.html` / `server.py` 后 `docker compose restart akanban` 即生效

备份只需复制这两个 `.db` 文件。

## 五、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 页面打开但所有数字是 `--` | 容器访问不到 ClickHouse 或后端未启动；先看 `docker compose logs` |
| 板块页无数据 | `CH_URL` 不可达；确认飞牛防火墙允许容器访问 18123 |
| 端口被占用 | 改 `.env` 的 `PORT`，如 `PORT=5050` |
| 时间显示偏差 | 确认 `.env`/compose 的 `TZ=Asia/Shanghai` |
| 外网 502 | Tunnel 的 Service 地址写错，应为 `http://akanban:5000` |
| 字体/图表加载慢 | 页面引用了飞书字体 CDN，外网正常；离线环境会回退系统字体 |
