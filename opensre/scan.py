from collections.abc import Sequence

from pydantic import BaseModel, Field, computed_field

from opensre.connectors.base import Connector
from opensre.domain import CheckResult, Finding, Severity


class ScanReport(BaseModel):
    findings: list[Finding] = Field(default_factory=list[Finding])
    errors: list[CheckResult] = Field(default_factory=list[CheckResult])

    @computed_field
    @property
    def ok(self) -> bool:
        return not self.findings and not self.errors


def run_checks(connectors: Sequence[Connector]) -> ScanReport:
    report = ScanReport()
    for connector in connectors:
        name = connector.name
        try:
            for check in connector.checks():
                name = f"{connector.name}/{check.name}"
                report.findings.extend(check.run())
        except Exception as exc:
            report.errors.append(CheckResult(name=name, ok=False, detail=str(exc)))
    order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
    report.findings.sort(
        key=lambda finding: (order[finding.severity], finding.resource, finding.reason)
    )
    return report
