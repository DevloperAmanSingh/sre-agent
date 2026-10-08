from collections.abc import Sequence

from langchain_core.tools import BaseTool

from opensre.config import Settings
from opensre.connectors.base import Connector


def collect_tools(connectors: Sequence[Connector]) -> list[BaseTool]:
    tools: list[BaseTool] = []
    names: set[str] = set()
    for connector in connectors:
        for tool in connector.tools():
            if (tool.metadata or {}).get("read_only") is not True:
                raise ValueError(f"Tool {tool.name} from {connector.name} is not marked read-only")
            if tool.name in names:
                raise ValueError(f"Duplicate connector tool: {tool.name}")
            names.add(tool.name)
            tools.append(tool)
    return tools


def build_connectors(settings: Settings) -> list[Connector]:
    connectors: list[Connector] = []
    if settings.connectors.kubernetes.enabled:
        from opensre.connectors.kubernetes.connector import KubernetesConnector

        connectors.append(KubernetesConnector(settings.connectors.kubernetes))
    collect_tools(connectors)
    return connectors
