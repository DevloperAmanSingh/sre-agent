import re

_ASSIGNMENT = re.compile(
    r"""(?i)(\b[\w.-]*(?:password|passwd|secret|token|api_key|apikey|key|credential|auth)[\w.-]*["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;}\]]+)"""
)
_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_URL = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")


def redact(text: str | None) -> str | None:
    if text is None:
        return None
    text = _URL.sub(r"\1[REDACTED]@", text)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    return _ASSIGNMENT.sub(r"\1[REDACTED]", text)
