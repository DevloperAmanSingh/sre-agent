import pytest

from opensre.agents.backend import SkillsBackend


@pytest.mark.parametrize("shadow", [False, True])
def test_skills_backend_reads_only_its_root(tmp_path, shadow):
    root = tmp_path / "skills"
    root.mkdir()
    (root / "SKILL.md").write_text("Triage playbook")
    (tmp_path / "secret").write_text("secret")
    (root / "escape").symlink_to(tmp_path / "secret")
    if shadow:
        (root / "memory").mkdir()
        (root / "memory/environment.md").write_text("Hidden stale fact")
    text = "Protected fact\nSecond fact"
    facts = {"/memory/environment.md": text}
    backend = SkillsBackend(root, facts=facts)
    facts["/memory/environment.md"] = "Mutated input"
    result = backend.read("/memory/environment.md", offset=1, limit=1)
    assert result.file_data["content"] == "Second fact"
    assert result.start_line == result.end_line == 2
    result.file_data["content"] = "Mutated result"
    assert backend.read("/memory/./environment.md").file_data["content"] == text
    assert "/memory/" in [item["path"] for item in backend.ls("/").entries]
    listing = backend.ls("/memory/")
    assert [item["path"] for item in listing.entries] == ["/memory/environment.md"]
    assert listing.error is None
    assert listing.entries[0]["size"] == len(text)
    for pattern, path in [("*.md", "/"), ("memory/*.md", "/"), ("*.md", "/memory")]:
        result = backend.glob(pattern, path)
        assert result.error is None
        matching = [item for item in result.matches if item["path"] == "/memory/environment.md"]
        assert len(matching) == 1
        assert matching[0]["size"] == len(text)
    matches = backend.grep("fact", "/", glob="*.md", max_count=1)
    assert matches.matches == [
        {"path": "/memory/environment.md", "line": 1, "text": "Protected fact"}
    ]
    assert matches.truncated
    assert backend.grep("Hidden", "/").matches == []
    assert backend.grep("Second", "/memory/environment.md").matches[0]["line"] == 2
    assert backend.grep("Protected", "/memory", glob="*.md").error is None
    assert backend.download_files(["/memory/environment.md"])[0].content == text.encode()
    with pytest.raises(NotImplementedError):
        backend.write("/memory/environment.md", "bad")
    with pytest.raises(NotImplementedError):
        backend.edit("/memory/environment.md", "Protected", "Changed")
    assert backend.download_files(["/memory/environment.md"])[0].content == text.encode()
    assert "Triage playbook" in str(backend.read("/SKILL.md"))
    for path in ["/../secret", "/escape"]:
        with pytest.raises(ValueError, match="not allowed|outside root"):
            backend.read(path)
    with pytest.raises(NotImplementedError):
        backend.write("/created", "bad")
    with pytest.raises(NotImplementedError):
        backend.edit("/SKILL.md", "Triage", "Changed")
    assert (root / "SKILL.md").read_text() == "Triage playbook"
    assert not (root / "created").exists()
