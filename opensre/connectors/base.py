from typing import Protocol

from langchain_core.tools import BaseTool

from opensre.domain import CheckResult, QuickCheck


class Connector(Protocol):
    name: str

    def health(self) -> CheckResult: ...

    def tools(self) -> list[BaseTool]: ...

    def checks(self) -> list[QuickCheck]: ...
