from opensre.domain import CheckResult, Evidence, Finding, QuickCheck, Severity


def test_quick_check_returns_typed_evidence():
    finding = Finding(
        summary="No namespaces visible",
        severity=Severity.WARNING,
        evidence=[Evidence(source="namespaces", detail="Empty list")],
    )
    check = QuickCheck(name="namespace-access", run=lambda: [finding])
    assert check.run()[0].model_dump(mode="json") == {
        "summary": "No namespaces visible",
        "severity": "warning",
        "evidence": [{"source": "namespaces", "detail": "Empty list"}],
    }


def test_health_detail_is_bounded():
    result = CheckResult(name="target", ok=False, detail="x" * 2010)
    assert result.detail == "x" * 2000 + "… [10 characters cut]"
