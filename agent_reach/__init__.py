# -*- coding: utf-8 -*-
"""Agent Reach — Give your AI Agent eyes to see the entire internet."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "1.5.0"
__author__ = "Neo Reid"

if TYPE_CHECKING:
    from agent_reach.core import AgentReach

__all__ = ["AgentReach"]


def __getattr__(name: str) -> Any:
    """Keep the existing top-level class export without eager host state."""

    if name == "AgentReach":
        from agent_reach.core import AgentReach

        return AgentReach
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
