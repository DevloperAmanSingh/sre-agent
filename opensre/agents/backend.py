from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from deepagents.backends import FilesystemBackend, StateBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    FileDownloadResponse,
    FileInfo,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
)


class _SnapshotReader(StateBackend):
    def __init__(self, facts: dict[str, str]) -> None:
        self.facts = MappingProxyType(dict(facts))

    def _read_files(self) -> dict[str, Any]:
        return {path: {"content": text, "encoding": "utf-8"} for path, text in self.facts.items()}

    def _send_files_update(self, update: dict[str, Any]) -> None:
        raise NotImplementedError("Facts are immutable")


def virtual_path(path: str) -> str:
    if ".." in PurePosixPath(path).parts:
        raise ValueError("Parent traversal is not allowed")
    return str(PurePosixPath("/" + path.lstrip("/")))


def merge_entries(disk: list[FileInfo] | None, facts: list[FileInfo] | None) -> list[FileInfo]:
    entries = {item["path"]: item for item in [*(disk or []), *(facts or [])]}
    return [entries[path] for path in sorted(entries)]


class SkillsBackend(BackendProtocol):
    def __init__(self, root: Path, *, facts: dict[str, str] | None = None) -> None:
        self.root = root
        self.reader = FilesystemBackend(root_dir=root, virtual_mode=True)
        self.snapshot = _SnapshotReader(facts or {})

    def _virtual_only(self, path: str) -> bool:
        return path in self.snapshot.facts or (
            any(key.startswith(path.rstrip("/") + "/") for key in self.snapshot.facts)
            and not (self.root / path.lstrip("/")).is_dir()
        )

    def ls(self, path: str) -> LsResult:
        path = virtual_path(path)
        if path in self.snapshot.facts:
            return LsResult(error=f"Path '{path}': not_a_directory")
        disk = LsResult(entries=[]) if self._virtual_only(path) else self.reader.ls(path)
        facts = self.snapshot.ls(path)
        return LsResult(error=disk.error, entries=merge_entries(disk.entries, facts.entries))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        file_path = virtual_path(file_path)
        reader = self.snapshot if file_path in self.snapshot.facts else self.reader
        return reader.read(file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        path = virtual_path(path or "/")
        facts = self.snapshot.grep(pattern, path, glob)
        if facts.error:
            return facts
        disk = (
            GrepResult(matches=[])
            if self._virtual_only(path)
            else self.reader.grep(pattern, path, glob)
        )
        matches = [
            *(facts.matches or []),
            *(item for item in disk.matches or [] if item["path"] not in self.snapshot.facts),
        ]
        truncated = disk.truncated or (max_count is not None and len(matches) > max_count)
        return GrepResult(error=disk.error, matches=matches[:max_count], truncated=truncated)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        path = virtual_path(path or "/")
        facts = self.snapshot.glob(pattern, path)
        if facts.error:
            return facts
        disk = (
            GlobResult(matches=[]) if self._virtual_only(path) else self.reader.glob(pattern, path)
        )
        return GlobResult(
            error=disk.error,
            matches=merge_entries(disk.matches, facts.matches),
            truncated=disk.truncated,
            truncation_reason=disk.truncation_reason,
        )

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results: list[FileDownloadResponse] = []
        for path in paths:
            normalized = virtual_path(path)
            if normalized in self.snapshot.facts:
                results.append(
                    FileDownloadResponse(
                        path=path, content=self.snapshot.facts[normalized].encode("utf-8")
                    )
                )
            else:
                results.extend(self.reader.download_files([path]))
        return results
