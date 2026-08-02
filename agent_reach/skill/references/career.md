# 职场招聘

LinkedIn。

## LinkedIn

LinkedIn 搜索使用本机回环地址上的
`linkedin-scraper-mcp==4.14.0` 服务。服务固定监听
`http://127.0.0.1:8001/mcp`；启动时将工具超时设为 12 秒，并将日志级别设为
`WARNING`、`ERROR` 或 `CRITICAL`，因为 `INFO` 日志可能包含搜索词。

```bash
# 搜索人才
mcporter --config /path/to/linkedin-people.json \
  call 'linkedin.search_people(keywords: "AI engineer")'

# 搜索职位
mcporter --config /path/to/linkedin-jobs.json \
  call 'linkedin.search_jobs(keywords: "software engineer", max_pages: 1)'
```

参数名是 `keywords`，不是 `keyword`。人才搜索没有后端 `limit` 参数；职位搜索的
`max_pages` 固定为 `1`。公开的结果数量限制只裁剪返回文档中的引用和职位 ID，
不会改变后端搜索参数。

结构化 `execution.v1` 路径为两种操作使用两个独立的 mcporter 配置。配置必须禁用
imports，并且每份配置只能允许当前操作对应的一个工具：

```json
{"imports":[],"mcpServers":{"linkedin":{"allowedTools":["search_people"],"baseUrl":"http://127.0.0.1:8001/mcp"}}}
```

```json
{"imports":[],"mcpServers":{"linkedin":{"allowedTools":["search_jobs"],"baseUrl":"http://127.0.0.1:8001/mcp"}}}
```

> **需要登录**: LinkedIn scraper 需要有效的本机浏览器登录态。登录态留在受信任的
> 本机服务中，不发送给远程或不受信任主机。

LinkedIn 搜索没有 Jina Reader fallback。MCP 服务、产物校验或登录态不可用时，
操作应失败关闭；不要改用网页读取器、任意 MCP 配置或服务的其他工具。
