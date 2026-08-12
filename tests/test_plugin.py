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


def test_marketplace_exposes_root_plugin() -> None:
    marketplace_path = ROOT / ".agents" / "plugins" / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))

    assert marketplace["name"] == "notebook-workbench"
    assert marketplace["interface"]["displayName"] == "Notebook Workbench"
    assert marketplace["plugins"] == [
        {
            "name": "notebook-workbench",
            "source": {"source": "local", "path": "./"},
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Developer Tools",
        }
    ]
    plugin_path = (
        marketplace_path.parent.parent.parent
        / marketplace["plugins"][0]["source"]["path"]
    )
    assert (plugin_path / ".codex-plugin" / "plugin.json").is_file()
