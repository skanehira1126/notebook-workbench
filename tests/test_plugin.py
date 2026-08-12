import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_manifest_declares_only_existing_skill_component() -> None:
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "notebook-workbench"
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert "apps" not in manifest
    assert (ROOT / "skills" / "notebook-workbench" / "SKILL.md").is_file()
