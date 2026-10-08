from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from langchain_core.tools import BaseTool

from opensre.config import Settings
from opensre.connectors.base import Connector

READ_TOOLS = frozenset({"ls", "read_file", "glob", "grep"})
RESERVED_TOOLS = READ_TOOLS | {"execute", "write_file", "edit_file", "task", "Diagnosis"}


@dataclass(frozen=True)
class ToolSnapshot:
    tools: tuple[BaseTool, ...]
    sources: Mapping[str, str]


def collect_tools(connectors: Sequence[Connector]) -> ToolSnapshot:
    tools: list[BaseTool] = []
    sources: dict[str, str] = {}
    for connector in connectors:
        for tool in connector.tools():
            if (tool.metadata or {}).get("read_only") is not True:
                raise ValueError(f"Tool {tool.name} from {connector.name} is not marked read-only")
            if tool.name in RESERVED_TOOLS:
                raise ValueError(f"Connector tool {tool.name} uses a reserved name")
            if tool.name in sources:
                raise ValueError(f"Duplicate connector tool: {tool.name}")
            sources[tool.name] = connector.name
            tools.append(tool)
    return ToolSnapshot(tuple(tools), MappingProxyType(sources))


def build_connectors(settings: Settings, *, namespace: str | None = None) -> list[Connector]:
    connectors: list[Connector] = []
    if settings.connectors.kubernetes.enabled:
        from opensre.connectors.kubernetes.connector import KubernetesConnector

        kube = settings.connectors.kubernetes
        if namespace is not None:
            kube = kube.model_copy(update={"namespace": namespace})
        connectors.append(KubernetesConnector(kube))
    return connectors
