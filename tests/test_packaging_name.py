"""Lock the Atomaestro distribution name without renaming the CLI."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI
    import tomli as tomllib

from mat_viewer.capabilities import DIST_NAME, install_command

ROOT = Path(__file__).resolve().parents[1]


def test_project_name_is_atomaestro_and_cli_stays_mat_vis() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert project["name"] == "atomaestro"
    assert project["scripts"] == {"mat-vis": "mat_viewer.cli:main"}
    assert "atomaestro" not in project["scripts"]
    renderers = project["entry-points"]["mattervis.tui.renderers"]
    assert renderers["mattervis.native-v1"] == (
        "mat_viewer.tui.render_backend:create_native_renderer"
    )
    assert project["optional-dependencies"]["dev"][0] == "atomaestro[all,test]"
    assert DIST_NAME == "atomaestro"
    assert install_command(()) == 'python -m pip install "atomaestro"'
    assert install_command(("gpu",)) == 'python -m pip install "atomaestro[gpu]"'


def test_release_workflow_publishes_only_the_new_project() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "name: pypi" in workflow
    assert "https://pypi.org/project/atomaestro/" in workflow
    assert "id-token: write" in workflow
    assert "attestations: true" in workflow
    assert "github.repository == 'SchrodingersCattt/Atomaestro'" in workflow
    assert 'importlib.metadata.version("atomaestro")' in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert 'importlib.metadata.version("matter-vis")' not in workflow
    assert "https://pypi.org/project/matter-vis/" not in workflow
