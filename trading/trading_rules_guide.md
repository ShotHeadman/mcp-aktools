# 交易规则配置指南

## 概述

MCP AkTools 现在支持自定义交易规则文件。当 AI 分析股票走势和给出买卖建议时，会以你配置的规则文件为最高优先级基准。

## 快速开始

### 1. 创建规则文件

复制项目根目录的 `trading_rules.example.md` 作为模板：

```bash
cp trading_rules.example.md my_trading_rules.md
```

根据你的交易策略修改文件内容。

### 2. 配置 MCP

在你的 MCP 配置文件中添加 `TRADING_RULE_FILE` 环境变量：

**Cursor / VS Code / Claude Desktop:**

```json
{
  "mcpServers": {
    "mcp-aktools": {
      "command": "uvx",
      "args": ["mcp-aktools"],
      "env": {
        "TRADING_RULE_FILE": "/Users/yourname/my_trading_rules.md"
      }
    }
  }
}
```

**Docker 部署:**

```yaml
# docker-compose.yml
services:
  mcp-aktools:
    image: ghcr.io/aahl/mcp-aktools:latest
    volumes:
      - ./my_trading_rules.md:/app/trading_rules.md:ro
    environment:
      - TRADING_RULE_FILE=/app/trading_rules.md
```

### 3. 使用规则

AI 会自动调用 `get_trading_rules` 工具获取你的规则。你也可以主动提醒 AI：

```
请先查看我的交易规则，然后分析 600519 这只股票是否值得买入
```

## 规则文件结构建议

一个完整的交易规则文件应该包含：

### 1. 风险控制
- 仓位管理（单只股票、单个板块的持仓比例）
- 止损止盈线（明确的百分比）
- 移动止损策略

### 2. 买入规则
- **技术指标条件**：MACD、KDJ、RSI、布林带等
- **基本面条件**：PE、PB、ROE、营收增长率等
- **市场环境**：大盘趋势、板块资金流、成交量等

### 3. 卖出规则
- **止损条件**（必须执行）
- **止盈条件**（分批卖出策略）
- **技术指标卖出信号**

### 4. 持仓管理
- 加仓条件和比例
- 减仓条件和比例

### 5. 禁止交易
- 不买入的股票类型
- 不持有的情况

### 6. 特殊情况
- 大盘暴跌时的应对
- 个股异常波动的处理

## 示例规则片段

```markdown
## 买入规则

### 技术指标条件（必须同时满足至少 3 个）

1. **MACD 金叉**
   - DIF 上穿 DEA
   - MACD 柱状图由负转正

2. **KDJ 超卖反弹**
   - K 值和 D 值均小于 30
   - K 值上穿 D 值

3. **RSI 合理区间**
   - RSI 值在 30-50 之间
   - 避免 RSI > 70 的超买区域

### 基本面条件（必须全部满足）

- 市盈率 PE < 30
- 净资产收益率 ROE > 10%
- 近三年营收增长率 > 15%

### 市场环境

- 大盘趋势向上或横盘整理
- 成交量放大（至少是 5 日均量的 1.5 倍）
```

## 工作原理

1. **规则加载**：MCP 启动时从环境变量读取规则文件路径
2. **规则获取**：AI 调用 `get_trading_rules` 工具获取规则内容
3. **规则应用**：AI 在分析时将规则作为最高优先级参考
4. **建议生成**：`trading_suggest` 工具会基于规则给出建议

## 最佳实践

### ✅ 推荐做法

1. **明确具体**：使用具体的数值而不是模糊的描述
   - ✅ "止损线 -8%"
   - ❌ "适当止损"

2. **分级处理**：为不同情况设置不同的应对策略
   - 轻度风险、中度风险、高度风险

3. **定期更新**：根据市场变化和交易经验更新规则

4. **版本管理**：使用 Git 管理规则文件的变更历史

### ❌ 避免的做法

1. 规则过于复杂，导致 AI 难以理解
2. 规则相互矛盾
3. 缺少明确的数值标准
4. 忽略风险控制

## 高级用法

### 多策略管理

为不同的交易风格创建不同的规则文件：

```bash
trading_rules_conservative.md  # 保守策略
trading_rules_aggressive.md    # 激进策略
trading_rules_swing.md         # 波段策略
```

切换策略时只需修改环境变量：

```json
{
  "env": {
    "TRADING_RULE_FILE": "/path/to/trading_rules_conservative.md"
  }
}
```

### 规则模板化

使用变量占位符（需要手动替换）：

```markdown
## 风险控制

- 单只股票持仓不超过 {{MAX_POSITION_PERCENT}}%
- 止损线：{{STOP_LOSS_PERCENT}}%
- 止盈线：{{TAKE_PROFIT_PERCENT}}%
```

## 常见问题

**Q: 规则文件支持哪些格式？**
A: 目前支持纯文本和 Markdown 格式，推荐使用 Markdown 以获得更好的可读性。

**Q: 规则文件可以放在远程服务器吗？**
A: 当前版本仅支持本地文件路径，未来版本会考虑支持 HTTP URL。

**Q: AI 一定会遵循我的规则吗？**
A: AI 会将规则作为最高优先级参考，但最终建议仍需要你自己判断。规则越明确具体，AI 的建议越准确。

**Q: 可以在规则文件中使用中文吗？**
A: 完全可以，建议使用 UTF-8 编码保存文件。

**Q: 如何验证规则文件是否加载成功？**
A: 可以直接询问 AI："请显示我的交易规则"，AI 会调用 `get_trading_rules` 工具返回规则内容。

## 技术支持

如有问题，请访问：
- GitHub Issues: https://github.com/aahl/mcp-aktools/issues
- 项目文档: https://github.com/aahl/mcp-aktools

---

**免责声明**：交易规则仅供参考，不构成投资建议。投资有风险，入市需谨慎。
