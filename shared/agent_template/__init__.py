"""Shared scaffolding for the "LangGraph sub-agent exposed via A2A" pattern.

Used by every A2A sub-agent in the mesh (flight, hotel, itinerary, critic,
research, todo) so the bridge between LangGraph and the A2A SDK lives in one
place rather than being copy-pasted six times.

Typical usage in a sub-agent's main.py:

    from shared.agent_template import LangGraphA2AExecutor

    async def _build_graph():
        tools = await MultiServerMCPClient({...}).get_tools()
        llm = ChatGoogleGenerativeAI(...).bind_tools(tools)
        ... build & compile StateGraph ...
        return compiled_graph

    executor = LangGraphA2AExecutor(_build_graph, agent_name="flight-agent")
    handler  = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    app      = A2ARESTFastAPIApplication(agent_card=..., http_handler=handler).build()
"""

from shared.agent_template.executor import LangGraphA2AExecutor

__all__ = ["LangGraphA2AExecutor"]
