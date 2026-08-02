# -*- coding: utf-8 -*-
"""LinkedIn — check if linkedin-scraper-mcp is available."""

import shutil

from .base import Channel
from .mcporter import McporterConfigError, inspect_mcporter_config

_LINKEDIN_SERVER_NAMES = {"linkedin", "linkedin-scraper", "linkedin-scraper-mcp"}


class LinkedInChannel(Channel):
    name = "linkedin"
    description = "LinkedIn 职业社交"
    backends = ["linkedin-scraper-mcp"]
    tier = 2

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "linkedin.com")

    def check(self, config=None):
        self.active_backend = None
        if not shutil.which("mcporter"):
            return "off", (
                "LinkedIn 搜索需要固定的本机 MCP 服务，没有网页读取 fallback：\n"
                "  pip install linkedin-scraper-mcp==4.14.0 "
                "mcp-server-linkedin==4.14.0\n"
                "  mcporter config add linkedin http://127.0.0.1:8001/mcp "
                "--scope home\n"
                "  执行时还需按操作使用仅允许 search_people 或 search_jobs 的配置，"
                "服务工具超时固定为 12 秒，日志级别至少为 WARNING。\n"
                "  详见 https://github.com/stickerdaniel/linkedin-mcp-server"
            )
        try:
            inspection = inspect_mcporter_config()
        except McporterConfigError as exc:
            return "error", f"mcporter 配置检查失败：{exc}"
        if inspection.server_names & _LINKEDIN_SERVER_NAMES:
            return "warn", (
                "LinkedIn MCP 已写入 mcporter 配置，但 Doctor 未启动本地"
                "服务做连通验证，不能仅凭配置宣称完整可用。"
            )
        if inspection.imports_unchecked:
            return "warn", (
                "mcporter 本地配置未发现 LinkedIn MCP；配置还启用了 editor "
                "imports，Doctor 为避免扩大凭据读取范围没有展开，当前未验证。"
            )
        return "off", (
            "mcporter 已装但 LinkedIn MCP 未配置。运行：\n"
            "  pip install linkedin-scraper-mcp==4.14.0 "
            "mcp-server-linkedin==4.14.0\n"
            "  mcporter config add linkedin http://127.0.0.1:8001/mcp "
            "--scope home\n"
            "  执行时还需按操作使用仅允许 search_people 或 search_jobs 的配置，"
            "服务工具超时固定为 12 秒，日志级别至少为 WARNING。"
        )
