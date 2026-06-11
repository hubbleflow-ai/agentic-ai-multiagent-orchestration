"""Phoenix tracing · cross-service trace tree with optional LangSmith dual-sink.

Phase 8 of the Session 6 refactor.

What this gives you (when wired):
  - Every LangChain / LangGraph call inside any service emits a span (via
    openinference-instrumentation-langchain).
  - Every inbound HTTP request to a FastAPI service is its own root span
    (via opentelemetry-instrumentation-fastapi); the W3C TraceContext
    header (`traceparent`) is extracted so the span attaches to whatever
    upstream trace called it.
  - Every outbound httpx request injects the same `traceparent` header on
    the way out (via opentelemetry-instrumentation-httpx). The a2a-sdk
    client + langchain-mcp-adapters MCP client both use httpx under the
    hood, so this covers all cross-service hops.

Net effect: a single trace in Phoenix shows
    Frontend → Planner.agent_stream
              ↳ Planner LLM (model node)
              ↳ delegate_to_flight_agent  (Planner @tool)
                  ↳ POST flight-agent /  (A2A SendMessage)
                      ↳ flight-agent LangGraph
                          ↳ flight-agent LLM (model node)
                          ↳ POST mcp-airline /mcp  (MCP CallToolRequest)
                              ↳ mcp-airline.search_flights
                                  ↳ Gemini structured output call
              ↳ ... (all the other tools and agents)

Project layout: ONE Phoenix project (agentic-ai-multiagent-orchestration)
containing all spans. Each span carries a `service.name` resource attribute
so you can filter/group in the Phoenix UI by service.

LangSmith dual-sink: if LANGSMITH_API_KEY is set, langchain-core's built-in
tracer publishes the same LangChain spans to LangSmith. This is enabled by
flipping LANGSMITH_TRACING=true · we do that here only when the key is
present, so unset = silent (no errors).

Init order matters: call `setup_observability(service_name)` BEFORE
importing langchain modules so the instrumentors patch fresh imports.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_initialized: set[str] = set()

PROJECT_NAME = os.environ.get("PHOENIX_PROJECT_NAME", "agentic-ai-multiagent-orchestration")


def setup_observability(service_name: str) -> None:
    """Wire Phoenix span collector + auto-instrument LangChain / FastAPI / httpx.

    Idempotent per service_name · second call within the same process is a
    no-op (necessary because importing this module from multiple entry
    points would otherwise double-instrument).
    """
    if service_name in _initialized:
        return
    _initialized.add(service_name)

    collector = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "").rstrip("/")
    langsmith_key = os.environ.get("LANGSMITH_API_KEY", "")

    # LangSmith side-channel · flip the tracing env on if a key is set so
    # langchain-core's built-in tracer starts publishing. This is a no-op
    # if the user hasn't provided a key.
    if langsmith_key:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")  # legacy var
        os.environ.setdefault(
            "LANGSMITH_PROJECT",
            os.environ.get("LANGSMITH_PROJECT", PROJECT_NAME),
        )

    if not collector:
        log.info(
            "observability.no_phoenix_collector service=%s · spans not exported",
            service_name,
        )
        return

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from phoenix.otel import register

        # One project for the entire mesh · spans carry service.name so
        # you can split/group in the Phoenix UI. The endpoint must include
        # /v1/traces (OTLP HTTP convention).
        tracer_provider = register(
            project_name=PROJECT_NAME,
            endpoint=f"{collector}/v1/traces",
            resource=Resource.create({"service.name": service_name}),
            set_global_tracer_provider=True,
        )

        # Order matters · instrument once, before the libraries finish
        # initialising under any load. The instrumentor classes are
        # idempotent if you call instrument() twice but better not to.
        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
        FastAPIInstrumentor().instrument(tracer_provider=tracer_provider)
        HTTPXClientInstrumentor().instrument(tracer_provider=tracer_provider)

        log.info(
            "observability.initialized service=%s project=%s collector=%s langsmith=%s",
            service_name, PROJECT_NAME, collector, bool(langsmith_key),
        )
    except Exception as e:
        # We never want observability setup to take down a service · log
        # loudly and continue without tracing.
        log.exception(
            "observability.init_failed service=%s err=%s · service continues without tracing",
            service_name, e,
        )
