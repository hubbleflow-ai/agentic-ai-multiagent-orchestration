"""Phoenix + LangSmith observability setup, shared by every service.

Call `setup_observability(service_name)` at the top of each service's main.py
BEFORE importing any LangChain module so the instrumentor patches cleanly.
"""

from shared.observability.phoenix_setup import setup_observability

__all__ = ["setup_observability"]
