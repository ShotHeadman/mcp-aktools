# 交易规则功能更新日志

## 新增功能

### 1. 交易规则配置支持

现在可以通过环境变量 `TRADING_RULE_FILE` 指定自定义交易规则文件，AI 在分析股票走势和给出买卖建议时会以该规则为最高优先级基准。

### 2. 新增工具

- **`get_trading_rules`**: 获取用户配置的交易规则文件内容
- **`trading_suggest`**: 更新描述，提示应先调用 `get_trading_rules` 获取规则

### 3. 配置文件更新

#### `mcp_aktools/config.py`
- 新增配置项：`trading_rule_file: Optional[str]`
- 新增函数：`get_trading_rules()` - 读取交易规则文件内容
- 支持绝对路径和相对路径
- 自动处理文件不存在的情况

#### `mcp_aktools/server.py`
- 注册新工具 `get_trading_rules`
- 更新 `trading_suggest` 工具描述

#### `.env.example`
- 新增 `TRADING_RULE_FILE` 配置说明

#### `docker-compose.yml`
- 新增交易规则文件挂载示例
- 新增 `TRADING_RULE_FILE` 环境变量配置

### 4. 文档更新

#### 新增文档
- **`trading_rules.example.md`**: 交易规则示例模板
  - 风险控制规则（仓位管理、止损止盈）
  - 买入规则（技术指标、基本面、市场环境）
  - 卖出规则（止损、止盈、技术信号）
  - 持仓规则（加仓、减仓条件）
  - 禁止交易条件
  - 特殊情况处理

- **`trading/trading_rules_guide.md`**: 交易规则配置详细指南
  - 快速开始教程
  - 规则文件结构建议
  - 工作原理说明
  - 最佳实践
  - 常见问题解答

- **`PUBLISHING.md`**: 发布到公共平台指南
  - PyPI 发布步骤
  - Smithery 发布步骤
  - Docker Hub / GHCR 发布步骤
  - MCP Registry 注册步骤
  - Glama.ai 提交步骤
  - 交易规则文件配置方案
  - 发布检查清单

#### 更新文档
- **`README.md`**: 
  - 新增"交易规则配置"章节
  - 更新工具列表，添加 `get_trading_rules`
  - 添加配置示例
  - 添加文档链接

### 5. 测试脚本

- **`test_rules_simple.py`**: 简单测试脚本
  - 测试绝对路径读取
  - 测试相对路径读取
  - 验证规则文件内容完整性

## 使用方法

### 配置示例

#### uvx 方式
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

#### Docker 方式
```yaml
services:
  mcp-aktools:
    image: ghcr.io/aahl/mcp-aktools:latest
    volumes:
      - ./trading_rules.md:/app/trading_rules.md:ro
    environment:
      - TRADING_RULE_FILE=/app/trading_rules.md
```

### AI 使用示例

用户可以这样与 AI 交互：

```
用户: 请先查看我的交易规则，然后分析 600519 这只股票是否值得买入

AI: [调用 get_trading_rules 工具]
    [调用 stock_info 工具]
    [调用 stock_prices 工具]
    [根据规则分析]
    [调用 trading_suggest 给出建议]
```

## 技术实现

### 配置加载流程
1. MCP 启动时从环境变量读取 `TRADING_RULE_FILE`
2. 存储在 `Settings` 对象中
3. 通过 `get_trading_rules()` 函数读取文件内容
4. 支持绝对路径和相对路径
5. 自动处理文件不存在的情况

### 工具调用流程
1. AI 调用 `get_trading_rules` 工具
2. 工具返回规则文件内容
3. AI 将规则作为最高优先级参考
4. 结合其他工具数据进行分析
5. 调用 `trading_suggest` 给出建议

## 兼容性

- 向后兼容：未配置 `TRADING_RULE_FILE` 时功能正常工作
- 文件格式：支持纯文本和 Markdown
- 编码：UTF-8
- 路径：支持绝对路径和相对路径

## 发布平台支持

### 已支持
- ✅ PyPI (uvx)
- ✅ Docker Hub / GHCR
- ✅ 本地部署

### 部分支持
- ⚠️ Smithery: 需要通过环境变量或远程 URL 配置规则文件

### 未来计划
- 🔄 支持从远程 URL 加载规则文件
- 🔄 支持规则文件热更新
- 🔄 支持多规则文件切换

## 测试验证

运行测试脚本验证功能：

```bash
python test_rules_simple.py
```

预期输出：
- ✓ 成功读取规则文件
- ✓ 绝对路径和相对路径都能正常工作
- ✓ 规则文件内容完整

## 相关文件

### 核心代码
- `mcp_aktools/config.py`
- `mcp_aktools/server.py`

### 配置文件
- `.env.example`
- `docker-compose.yml`

### 文档
- `README.md`
- `trading_rules.example.md`
- `trading/trading_rules_guide.md`
- `PUBLISHING.md`

### 测试
- `test_rules_simple.py`

## 下一步

1. **发布新版本**
   - 更新版本号到 0.1.14
   - 发布到 PyPI
   - 更新 Docker 镜像

2. **推广**
   - 更新 Smithery 部署
   - 提交到 MCP Registry
   - 在 Glama.ai 更新信息

3. **收集反馈**
   - 用户使用体验
   - 规则文件格式建议
   - 功能改进建议

## 贡献者

- 实现交易规则配置功能
- 编写完整文档和示例
- 创建测试脚本

---

**版本**: 0.1.14 (待发布)  
**日期**: 2025-11-26
