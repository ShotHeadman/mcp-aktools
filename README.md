# AkTools MCP Server

基于 akshare 的 MCP (Model Context Protocol) 服务器，提供股票数据查询和分析功能。


## 功能

- 股票搜索、股票信息、历史价格和分时数据
- A股市场概况、涨停池、龙虎榜、资金流向
- 个股新闻、全球财经快讯
- A股、港股、美股财务指标
- 量化回测和自定义指标


## 使用

### 公网服务

当前已部署到 FastMCP Cloud / Prefect Horizon:

```text
https://shotheadman.fastmcp.app/mcp
```

支持 Streamable HTTP 的 MCP 客户端可直接使用 `url` 方式连接:

```json
{
  "mcpServers": {
    "mcp-aktools": {
      "url": "https://shotheadman.fastmcp.app/mcp"
    }
  }
}
```

如果在 Horizon/FastMCP Cloud 中开启了 Authentication，客户端会按 Horizon 的 OAuth 流程完成授权；如果关闭 Authentication，该地址即为公开可访问的 MCP 服务。

### 部署到 Horizon

1. 将本仓库推送到 GitHub。
2. 打开 [Prefect Horizon](https://horizon.prefect.io/) 并使用 GitHub 登录。
3. 选择仓库并配置 Server:
   - Server name: 自定义，例如 `shotheadman`
   - Entrypoint: `mcp_aktools/__init__.py:mcp`
   - Authentication: 按需开启；公开服务可关闭
4. 点击 Deploy Server。
5. 部署成功后使用生成的地址连接，格式通常为:

```text
https://<server-name>.fastmcp.app/mcp
```

本项目的 FastMCP 实例定义在 `mcp_aktools/__init__.py` 中:

```python
mcp = create_mcp_server()
```

Horizon 会根据 `pyproject.toml` 自动安装依赖；推送到 `main` 分支后会自动重新部署，并会为 PR 创建预览环境。

### 本地 stdio

```yaml
{
  "mcpServers": {
    "mcp-aktools": {
      "command": "uvx",
      "args": ["mcp-aktools"],
      "env": {
        "NEWSNOW_BASE_URL": "https://newsnow.busiyi.world",
        "NEWSNOW_CHANNELS": "wallstreetcn-quick,cls-telegraph,jin10"
      }
    }
  }
}
```

其中 `mcp-aktools` 是包名，`mcpServers` 里的 `mcp-aktools` 是客户端中显示的服务名称，可按需改成 `aktools`、`stock-tools` 等别名。

### 本地 HTTP / 局域网

从源码运行:

```bash
uv sync --locked --no-dev
uv run python -m mcp_aktools --http --host 0.0.0.0 --port 8808
```

或使用已发布包:

```bash
TRANSPORT=http HOST=0.0.0.0 PORT=8808 uvx mcp-aktools
```

客户端配置:

```json
{
  "mcpServers": {
    "mcp-aktools": {
      "url": "http://127.0.0.1:8808/mcp"
    }
  }
}
```

如果要给局域网其他设备访问，请把 `127.0.0.1` 替换为本机局域网 IP。公网自部署时建议放在 Nginx/Caddy 后面，并配置 HTTPS 和访问控制。

### Docker

```bash
docker compose up -d
```

默认将容器内 `80` 端口映射到宿主机 `8808`:

```json
{
  "mcpServers": {
    "mcp-aktools": {
      "url": "http://127.0.0.1:8808/mcp"
    }
  }
}
```

也可以在服务器上使用远程 compose 文件部署:

```bash
mkdir -p /opt/mcp-aktools
cd /opt/mcp-aktools
wget https://raw.githubusercontent.com/shotheadman/mcp-aktools/refs/heads/main/docker-compose.yml
docker-compose up -d
```

### 环境变量

常用配置:

```bash
TRANSPORT=http
HOST=0.0.0.0
PORT=8808
DATABASE_URL=./data/stock_data.db
NEWSNOW_BASE_URL=
NEWSNOW_CHANNELS=wallstreetcn-quick,cls-telegraph,jin10
ENABLE_USER_CODE_EXECUTION=false
```

------

## 可用工具

### 高级配置

- `CACHE_DISK_TTL`: 非价格类 SQLite 磁盘缓存过期时间，单位秒；历史价格类缓存仍按持久缓存处理。
- `ENABLE_USER_CODE_EXECUTION`: 是否允许执行回测策略和自定义指标代码，默认 `false`。
- `USER_CODE_MAX_CHARS`: 用户代码最大长度，默认 `20000`。
- `USER_CODE_ALLOWED_IMPORTS`: 允许用户代码导入的模块列表，逗号分隔，默认不允许导入。
- `stock_prices` 默认返回结构化 JSON；如需旧 CSV 文本可传 `response_format=csv`。
- HTTP 请求默认忽略环境变量和系统代理设置，确保数据源访问行为可预测；如需替换 NewsNow 访问入口，请使用 `NEWSNOW_BASE_URL`。

### 工具列表

- `search` - 查找股票代码，支持A股、港股、美股
- `stock_info` - 获取股票信息
- `stock_prices` - 获取股票历史价格（日线/周线/月线，美股仅支持日线）
- `stock_zh_a_minute` - A股分时K线数据（支持1/5/15/30/60分钟，默认返回最近3个交易日数据，可自定义1-10个交易日，带备用接口）
- `stock_tick_data` - A股分笔成交数据（支持大单筛选、买卖盘统计等智能分析）
- `stock_indicators_a` / `stock_indicators_hk` / `stock_indicators_us` - 财务关键指标
- `get_current_time` - 获取当前时间及A股交易日信息
- `stock_zt_pool_em` - A股涨停股池
- `stock_zt_pool_strong_em` - A股强势股池
- `stock_lhb_ggtj_sina` - A股龙虎榜统计
- `stock_sector_fund_flow_rank` - A股概念资金流向
- `stock_news` / `stock_news_global` - 个股新闻和全球财经快讯
- `run_backtest` / `compare_strategies` - 运行和对比回测
- `register_indicator` / `evaluate_signals` - 自定义指标和技术信号评估

A 股日线缓存按调用的 TTL 刷新最后一根 K 线，周线和月线由日线聚合。
分钟回测使用分钟行情接口，可用历史区间受上游数据源限制。
涨停池、强势股池和炸板池仅返回指定日期的东方财富数据；数据源不可用时返回失败信息。
