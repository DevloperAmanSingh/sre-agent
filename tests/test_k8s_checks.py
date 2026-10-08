import pytest
from fakes.kubernetes import connector, container_status, page, pod


def run_rule(target, name):
    return next(check for check in target.checks() if check.name == name).run()


@pytest.mark.parametrize("init", [False, True])
def test_crashloop_detects_current_state_in_regular_and_init_containers(init):
    obj = pod([container_status("CrashLoopBackOff", ready=False)])
    if init:
        obj.status.init_container_statuses = obj.status.container_statuses
        obj.status.container_statuses = []
    target = connector(list_namespaced_pod=lambda **kwargs: page([obj]))
    findings = run_rule(target, "crashloop")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "critical"
    assert finding.resource == "pod/production/checkout"
    assert finding.reason == "CrashLoopBackOff"
    assert finding.evidence[0].source == "k8s_describe_pod"
    assert "app" in finding.evidence[0].detail
