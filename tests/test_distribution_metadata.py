"""PyPI upload preflight for direct Requires-Dist references and release tags."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from mat_viewer.capabilities import MOLCRYSKIT_DEVELOPMENT_REQUIREMENT

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "reject_direct_dependencies.py"


def _checker():
    spec = importlib.util.spec_from_file_location("reject_direct_dependencies", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_requirement_is_uploadable_and_direct_url_is_not() -> None:
    checker = _checker()
    allowed = checker.direct_reference_requirements(
        "Metadata-Version: 2.1\n"
        "Name: atomaestro\n"
        'Requires-Dist: molcrys-kit>=0.8.0,<0.9.0\n'
        "Requires-Dist: numpy\n"
    )
    blocked = checker.direct_reference_requirements(
        "Metadata-Version: 2.1\n"
        "Name: atomaestro\n"
        f"Requires-Dist: {MOLCRYSKIT_DEVELOPMENT_REQUIREMENT}\n"
    )
    assert allowed == []
    assert blocked == [MOLCRYSKIT_DEVELOPMENT_REQUIREMENT]
    omitted = checker.requires_dist_values(
        "Metadata-Version: 2.1\nName: atomaestro\nRequires-Python: >=3.10\n"
    )
    requires_txt = checker.requires_txt_as_metadata(
        f"{MOLCRYSKIT_DEVELOPMENT_REQUIREMENT.replace(' @ ', '@ ')}\n"
        "numpy\n\n[tui]\ntextual\n"
    )
    assert omitted == []
    assert checker.direct_reference_requirements(requires_txt) == [
        MOLCRYSKIT_DEVELOPMENT_REQUIREMENT.replace(" @ ", "@ ")
    ]
    assert checker.is_release_tag("v0.0.6")
    assert not checker.is_release_tag("v")
    assert not checker.is_release_tag("v1")
    assert not checker.is_release_tag("v1.2")
    assert not checker.is_release_tag("v1.2.3rc1")
    assert not checker.is_release_tag("v1.2.3-rc1")


def test_current_molcrys_pin_stays_and_would_be_rejected_on_upload() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert MOLCRYSKIT_DEVELOPMENT_REQUIREMENT in pyproject
    assert "2a3ee11e4c708b64691181cc2cbc4b2a3625f0c6" in pyproject
    assert "molcrys-kit==0.8.0" not in pyproject


def test_release_jobs_reject_direct_references_and_non_final_tags() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count("reject_direct_dependencies.py") == 3
    assert "startsWith(github.ref, 'refs/tags/v')" not in workflow
    assert workflow.count(
        "needs.release-checks.outputs.tag == github.ref_name"
    ) == 2
    assert '[[ ! "${GITHUB_REF_NAME}" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]' in workflow
    publish = workflow.split("publish-pypi:", 1)[1].split("create-github-release:", 1)[0]
    release = workflow.split("create-github-release:", 1)[1]
    build = workflow.split("build-package:", 1)[1].split("publish-pypi:", 1)[0]
    assert build.index("reject_direct_dependencies.py") < build.index(
        "actions/upload-artifact"
    )
    assert publish.index("reject_direct_dependencies.py") < publish.index(
        "pypa/gh-action-pypi-publish"
    )
    assert release.index("reject_direct_dependencies.py") < release.index(
        "gh release create"
    )
