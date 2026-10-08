import pytest


def test_facts_append_load_both_and_cap(tmp_path):
    from opensre.memory.facts import load_facts, remember

    directory = tmp_path / "state"
    assert load_facts(directory) == {}
    remember(directory, "  payments runs in ns shop  ")
    remember(directory, "token=private")
    remember(directory, "x" * 10000)
    project = tmp_path / ".opensre"
    project.mkdir()
    (project / "memory.md").write_text("Owner: team-pay password=private")
    facts = load_facts(directory)
    assert len(facts) == 2
    text = "\n".join(facts.values())
    assert "- payments runs in ns shop\n" in text
    assert "Owner: team-pay" in text
    assert "private" not in text
    assert "characters cut" in text
    assert len(text) < 12000
    assert "private" not in (directory / "memory/environment.md").read_text()


@pytest.mark.parametrize("text", ["", "  ", "one\ntwo", "one\rtwo"])
def test_remember_rejects_invalid_lines(tmp_path, text):
    from opensre.memory.facts import remember

    with pytest.raises(ValueError, match="single"):
        remember(tmp_path, text)


def test_bad_fact_file_warns_and_keeps_other_facts(tmp_path, caplog):
    from opensre.memory.facts import load_facts

    (tmp_path / "memory").mkdir()
    (tmp_path / "memory/environment.md").write_bytes(b"\xff")
    (tmp_path / ".opensre").mkdir()
    (tmp_path / ".opensre/memory.md").write_text("Good fact")
    assert list(load_facts(tmp_path).values()) == ["Good fact"]
    assert "Memory" in caplog.text
