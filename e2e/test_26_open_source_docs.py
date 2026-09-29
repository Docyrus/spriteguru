"""The open-source docs contract: the README alone takes a clean clone to a running synthetic studio,
and no active doc still sets up the retired cloud or the old command name."""

from __future__ import annotations

from conftest import ROOT


def test_readme_is_a_complete_clean_checkout_runbook():
    readme = (ROOT / "README.md").read_text()
    required = (
        "git clone https://github.com/Docyrus/spriteguru.git",
        "cd spriteguru",
        "Python 3.12",
        "Node.js",
        "uv sync",
        "npm ci",
        "npm run build",
        "uv run spriteguru init",
        "--mode synthetic",
        "uv run spriteguru studio",
        "SPRITEGURU_DEV",
        "packaging/build_app.sh",
        "Apache License 2.0",
    )
    assert [item for item in required if item not in readme] == []


def test_active_docs_have_no_cloud_setup_or_old_commands():
    assert not (ROOT / "docs" / "cloud-integration-plan.md").exists()
    active = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / ".env.example"]
    forbidden = ("spriteplay.com", "uv run spritekit", "cloud login", "sync --link")
    hits = [(str(path.relative_to(ROOT)), token) for path in active for token in forbidden if token in path.read_text()]
    assert hits == []


def test_env_example_matches_documented_provider_names():
    text = (ROOT / ".env.example").read_text()
    assert text.splitlines() == [
        "OPENAI_API_KEY=",
        "FAL_KEY=",
        "RD_API_KEY=",
        "QUIVERAI_API_KEY=",
    ]
