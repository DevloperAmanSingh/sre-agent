from langchain_core.tools import BaseTool

from opensre.config import KubeSettings
from opensre.connectors.kubernetes.health import check_kube
from opensre.domain import CheckResult, QuickCheck


class KubernetesConnector:
    name = "kubernetes"

    def __init__(self, settings: KubeSettings) -> None:
        self.settings = settings

    def health(self) -> CheckResult:
        return check_kube(self.settings)

    def tools(self) -> list[BaseTool]:
        return []

    def checks(self) -> list[QuickCheck]:
        return []
