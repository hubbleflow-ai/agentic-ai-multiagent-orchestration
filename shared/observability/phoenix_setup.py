"""Phoenix tracing + optional LangSmith dual-sink, init exactly once per service.

TODO(s6): port the implementation from
  agentic-ai-introduction/backend/app/observability.py
adapting the service_name → Phoenix project mapping so every service in the
mesh shows up as its own project (or all under one agentic-ai-multiagent-
orchestration project with service_name as a tag — pick when implementing).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def setup_observability(service_name: str) -> None:
    """Wire Phoenix span collector and (optionally) LangSmith.

    Idempotent: safe to call twice (second call is a no-op).
    Must be called BEFORE importing any LangChain module.
    """
    # TODO(s6): real implementation.
    collector = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
    langsmith = os.environ.get("LANGSMITH_API_KEY")

    log.info("observability.stub service=%s phoenix=%s langsmith=%s",
             service_name, bool(collector), bool(langsmith))
