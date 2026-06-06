"""A2A protocol scaffolding shared by every worker service.

Re-exports `A2AClient` and `A2AServer` so service code imports stay short.
"""

from shared.a2a.client import A2AClient
from shared.a2a.server import A2AServer

__all__ = ["A2AClient", "A2AServer"]
