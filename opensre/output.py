def cap_text(value: str, limit: int = 8000) -> str:
    if len(value) > limit:
        return f"{value[:limit]}… [{len(value) - limit} characters cut]"
    return value
