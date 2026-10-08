from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from kubernetes.client.rest import ApiException
from urllib3.exceptions import ReadTimeoutError

from opensre.config import KubeSettings


def test_reader_reports_known_and_unknown_page_totals():
    from opensre.connectors.kubernetes.reader import bounded_page

    for remaining, expected in [(262, "showing 50 of 312"), (None, "showing 50; more available")]:
        page = SimpleNamespace(
            items=list(range(50)),
            metadata=SimpleNamespace(remaining_item_count=remaining, _continue="next"),
        )
        result = bounded_page(page, 50, lambda item: str(item))
        assert len(result.items) == 50
        assert result.truncation == expected


@pytest.mark.parametrize(
    "status,code", [(403, "forbidden"), (404, "not_found"), (503, "unreachable")]
)
def test_reader_maps_errors_without_leaking_api_body(status, code):
    from opensre.connectors.kubernetes.reader import KubeReader, KubeReadError

    reader = KubeReader(KubeSettings(), lambda settings: nullcontext(object()))

    def fail(api):
        raise ApiException(status=status, reason="unsafe-secret", http_resp=None)

    with pytest.raises(KubeReadError) as error:
        reader.read(fail)
    assert error.value.error.code == code
    assert "unsafe-secret" not in str(error.value)


def test_reader_classifies_transport_timeouts():
    from opensre.connectors.kubernetes.reader import KubeReader, KubeReadError

    reader = KubeReader(KubeSettings(), lambda settings: nullcontext(object()))

    def fail(api):
        raise ReadTimeoutError(None, "/api", "request timed out")

    with pytest.raises(KubeReadError) as error:
        reader.read(fail)
    assert error.value.error.code == "timeout"


def test_reader_deadline_includes_client_creation(deadline):
    from opensre.connectors.kubernetes.reader import KubeReader, KubeReadError

    def credentials(settings):
        deadline()
        return nullcontext(object())

    with pytest.raises(KubeReadError) as error:
        KubeReader(KubeSettings(request_timeout_s=3), credentials).read(lambda api: None)
    assert error.value.error.code == "timeout"
    assert "timed out after 3s" in str(error.value)


def test_large_typed_results_are_bounded_in_content_and_artifact():
    from opensre.connectors.kubernetes.models import PodSummary
    from opensre.connectors.kubernetes.reader import Page, bounded_response

    result = Page[PodSummary](
        items=[
            PodSummary(
                name="x" * 4000,
                namespace="default",
                phase="Running",
                ready="1/1",
                restarts=0,
                age_s=0,
                node="worker",
            )
            for _ in range(100)
        ],
        cut=200,
        more_available=True,
        truncation="showing 100 of 300",
    )
    content, artifact = bounded_response(result)
    assert len(content) <= 20000
    assert content == artifact.model_dump_json()
    assert artifact.cut == 300 - len(artifact.items)
    assert artifact.output_cut > 0
    assert artifact.truncation == f"showing {len(artifact.items)} of 300"
    assert "characters cut" in artifact.items[0].name
