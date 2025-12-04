# 📈 AkTools MCP Server

<!-- mcp-name: io.github.shotheadman/mcp-aktools -->
<!-- [![MCP Badge](https://lobehub.com/badge/mcp/shotheadman-mcp-aktools)](https://lobehub.com/mcp/shotheadman-mcp-aktools) -->
<!-- [![Verified on MseeP](https://mseep.ai/badge.svg)](https://mseep.ai/app/1dd74d48-e77b-49f9-8d67-8c99603336e1) -->

基于 akshare 的 MCP (Model Context Protocol) 服务器，提供股票、加密货币的数据查询和分析功能。


## 功能

- 🔍 **股票搜索**: 根据股票名称、公司简称等关键词查找股票代码
- ℹ️ **股票信息**: 获取股票的详细信息，包括价格、市值等
- 📊 **市场概况**: 获取A股市场的涨停板、龙虎榜、资金流向等
- 💹 **历史价格**: 获取股票、加密货币历史价格数据，包含技术分析指标
- 📰 **相关新闻**: 获取股票、加密货币相关的最新新闻资讯
- 💸 **财务指标**: 支持A股和港美股的财务报告关键指标查询


## 安装

### 方式1: uvx
```yaml
{
  "mcpServers": {
    "mcp-aktools": {
      "command": "uvx",
      "args": ["mcp-aktools"],
      "env": {
        # 全部可选
        "OKX_BASE_URL": "https://okx.4url.cn", # OKX地址，如果你的网络环境无法访问okx.com，可通过此选项配置反代地址
        "BINANCE_BASE_URL": "https://bian.4url.cn", # 币安地址，默认: https://www.binance.com
        "NEWSNOW_BASE_URL": "https://newsnow.busiyi.world", # Newsnow接口地址
        "NEWSNOW_CHANNELS": "wallstreetcn-quick,cls-telegraph,jin10", # Newsnow资讯来源
        "TRADING_RULE_FILE": "/path/to/your/trading_rules.md", # 交易规则文件路径（可选）
      }
    }
  }
}
```

### 方式2: [Smithery](https://smithery.ai/server/@shotheadman/mcp-aktools)
> 需要通过OAuth授权或Smithery key

```yaml
{
  "mcpServers": {
    "mcp-aktools": {
      "url": "https://server.smithery.ai/@shotheadman/mcp-aktools/mcp" # Streamable HTTP
    }
  }
}
```

### 方式3: Docker
```bash
mkdir /opt/mcp-aktools
cd /opt/mcp-aktools
wget https://raw.githubusercontent.com/shotheadman/mcp-aktools/refs/heads/main/docker-compose.yml
docker-compose up -d
```
```yaml
{
  "mcpServers": {
    "mcp-aktools": {
      "url": "http://0.0.0.0:8808/mcp" # Streamable HTTP
    }
  }
}
```

### 快速开始
- 在线体验: [![fastmcp.cloud](https://img.shields.io/badge/Cloud-+?label=FastMCP)](https://fastmcp.cloud/xiaomi/aktools/chat)
- 在线体验: [![smithery.ai](https://smithery.ai/badge/@shotheadman/mcp-aktools)](https://smithery.ai/server/@shotheadman/mcp-aktools)
- 添加到 Cursor [![Install MCP Server](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/zh/install-mcp?name=aktools&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJtY3AtYWt0b29scyJdfQ%3D%3D)
- 添加到 VS Code [![Install MCP Server](https://img.shields.io/badge/VS_Code-+?label=Add+MCP+Server&color=0098FF)](https://insiders.vscode.dev/redirect?url=vscode:mcp/install%3F%7B%22name%22%3A%22aktools%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22mcp-aktools%22%5D%7D)
- 添加到 Cherry Studio [![Install MCP Server](https://img.shields.io/badge/Cherry_Studio-+?label=Add+MCP+Server&color=FF5F5F)](https://gitee.com/link?target=cherrystudio%3A%2F%2Fmcp%2Finstall%3Fservers%3DeyJtY3BTZXJ2ZXJzIjp7ImFrdG9vbHMiOnsiY29tbWFuZCI6InV2eCIsImFyZ3MiOlsibWNwLWFrdG9vbHMiXX19fQ%3D%3D)
- 添加到 Claude Code, 执行命令: `claude mcp add aktools -- uvx mcp-aktools`
- 添加到 OpenAI CodeX, 执行命令: `codex mcp add aktools -- uvx mcp-aktools`

------

## � 交易规则配置

MCP AkTools 支持自定义交易规则文件，在分析走势和买入卖出点时，AI 会以该规则文件为最高基准进行判断。

### 配置方法

在 MCP 配置中添加 `TRADING_RULE_FILE` 环境变量：

```json
{
  "mcpServers": {
    "mcp-aktools": {
      "command": "uvx",
      "args": ["mcp-aktools"],
      "env": {
        "TRADING_RULE_FILE": "/path/to/your/trading_rules.md"
      }
    }
  }
}
```

### 规则文件示例

项目提供了 `trading_rules.example.md` 作为参考模板，包含：
- 风险控制规则（仓位管理、止损止盈）
- 买入规则（技术指标、基本面、市场环境）
- 卖出规则（止损、止盈、技术信号）
- 持仓规则（加仓、减仓条件）
- 禁止交易条件
- 特殊情况处理

你可以根据自己的交易策略修改该文件。

### 使用方式

1. AI 在分析股票或加密货币时，会自动调用 `get_trading_rules` 工具获取你的交易规则
2. 在给出投资建议时，会严格遵循你配置的规则进行判断
3. 规则文件支持 Markdown 格式，便于阅读和维护

📖 **详细文档**：
- [交易规则配置指南](trading/trading_rules_guide.md)
- [发布到公共平台指南](PUBLISHING.md)

------

## 🛠️ 可用工具

<details>
<summary><strong>个股相关</strong></summary>

- `search` - 查找股票代码，支持A股、港股、美股
- `stock_info` - 获取股票信息
- `stock_prices` - 获取股票历史价格（日线/周线）
- `stock_zh_a_minute` - A股分时K线数据（支持1/5/15/30/60分钟，默认返回最近3个交易日数据，可自定义1-10个交易日，带备用接口）
- `stock_tick_data` - A股分笔成交数据（支持大单筛选、买卖盘统计等智能分析）
- `stock_indicators_a` - A股关键指标
- `stock_indicators_hk` - 港股关键指标
- `stock_indicators_us` - 美股关键指标
- `get_trading_rules` - 获取用户配置的交易规则
- `trading_suggest` - 给出投资建议

</details>

<details>
<summary><strong>A股市场</strong></summary>

- `get_current_time` - 获取当前时间及A股交易日信息
- `stock_zt_pool_em` - A股涨停股池
- `stock_zt_pool_strong_em` - A股强势股池
- `stock_lhb_ggtj_sina` - A股龙虎榜统计
- `stock_sector_fund_flow_rank` - A股概念资金流向

</details>

<details>
<summary><strong>财经资讯</strong></summary>

- `stock_news` - 获取个股/加密货币相关新闻
- `stock_news_global` - 全球财经快讯

</details>

<details>
<summary><strong>加密货币</strong></summary>

- `okx_prices` - 获取加密货币历史价格
- `okx_loan_ratios` - 获取加密货币杠杆多空比
- `okx_taker_volume` - 获取加密货币主动买卖情况
- `binance_ai_report` - 获取加密货币AI分析报告

</details>


------

<a href="https://glama.ai/mcp/servers/@al-one/mcp-aktools">
  <img width="400" src="https://glama.ai/mcp/servers/@al-one/mcp-aktools/badge">
</a>

[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/shotheadman-mcp-aktools-badge.png)](https://mseep.ai/app/shotheadman-mcp-aktools)
