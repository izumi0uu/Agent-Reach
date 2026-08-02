# 搜索工具

Exa AI 搜索引擎。

## Exa AI 搜索

高质量 AI 搜索引擎，擅长技术和代码搜索。

```bash
mcporter call 'exa.web_search_exa(query: "query", numResults: 5)'
mcporter --http-url 'https://mcp.exa.ai/mcp?tools=get_code_context_exa' \
  call 'exa.get_code_context_exa(query: "code question", numResults: 5)'
```

### 使用场景

| 场景 | 参数 |
|-----|------|
| 网页搜索 | `web_search_exa(query: "...", numResults: 5)` |
| 代码搜索 | 特殊 Exa MCP endpoint 的 `get_code_context_exa(query: "...", numResults: 5)` |

代码搜索只接受 `query` 和可选的 `numResults`。旧示例中的 `tokensNum` 不属于当前
接口，不能用它替代 `numResults`；代码搜索也不能退化成 `web_search_exa`。

### 特点

- 擅长英文内容和技术文档
- 支持代码上下文搜索
- 结果质量高

## 与其他搜索工具对比

| 工具 | 来源 | 适用场景 |
|-----|------|---------|
| Exa | agent-reach | 英文/技术/代码搜索 |
| 智谱搜索 | my-mcp-tools | 中文搜索 |
| GitHub 搜索 | agent-reach (dev.md) | 仓库/代码搜索 |
