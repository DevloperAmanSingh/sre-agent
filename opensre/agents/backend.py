from pathlib import Path

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    FileDownloadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
)


class SkillsBackend(BackendProtocol):
    def __init__(self, root: Path, *, facts: dict[str, str] | None = None) -> None:
        self.reader = FilesystemBackend(root_dir=root, virtual_mode=True)
        self.facts = dict(facts or {})

    def ls(self, path: str) -> LsResult:
        return self.reader.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self.reader.read(file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self.reader.grep(pattern, path, glob, max_count=max_count)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self.reader.glob(pattern, path)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(path=path, content=self.facts[path].encode("utf-8"))
            if path in self.facts
            else self.reader.download_files([path])[0]
            for path in paths
        ]
