import pytest

from opensre.agents.backend import SkillsBackend


def test_skills_backend_reads_only_its_root(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    (root / "SKILL.md").write_text("Triage playbook")
    (tmp_path / "secret").write_text("secret")
    (root / "escape").symlink_to(tmp_path / "secret")
    backend = SkillsBackend(root)
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
