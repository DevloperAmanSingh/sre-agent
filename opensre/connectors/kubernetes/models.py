from pydantic import BaseModel


class PodSummary(BaseModel):
    name: str
    namespace: str
    phase: str
    ready: str
    restarts: int
    age_s: int | None
    node: str | None
