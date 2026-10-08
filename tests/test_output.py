from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from opensre.agents.middleware import OutputCapMiddleware
from opensre.output import cap_text


@pytest.mark.parametrize("size", [0, 10, 13])
def test_text_cap_reports_exact_cut(size):
    expected = "x" * min(size, 10) + (f"… [{size - 10} characters cut]" if size > 10 else "")
    assert cap_text("x" * size, 10) == expected


@pytest.mark.parametrize("failure", [False, True])
def test_middleware_caps_success_and_errors(failure):
    def handler(request):
        if failure:
            raise ToolException("x" * 30)
        return ToolMessage(content="x" * 30, tool_call_id="read")

    request = SimpleNamespace(tool_call={"id": "read", "name": "read_file"})
    result = OutputCapMiddleware(limit=10).wrap_tool_call(request, handler)
    assert result.content == "x" * 10 + "… [20 characters cut]"
    assert result.status == ("error" if failure else "success")
    assert result.tool_call_id == "read"
