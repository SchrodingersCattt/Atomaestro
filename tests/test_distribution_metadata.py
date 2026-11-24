"""PyPI upload preflight for direct Requires-Dist references and release tags."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from mat_viewer.capabilities import MOLCRYSKIT_REQUIREMENT

DIRECT_REFERENCE = "molcrys-kit[desktop] @ https://example.invalid/molcrys_kit.whl"

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
        'Requires-Dist: molcrys-kit[desktop]>=0.9.0,<0.10.0\n'
        "Requires-Dist: numpy\n"
    )
    blocked = checker.direct_reference_requirements(
        "Metadata-Version: 2.1\n"
        "Name: atomaestro\n"
        f"Requires-Dist: {DIRECT_REFERENCE}\n"
    )
    assert allowed == []
    assert blocked == [DIRECT_REFERENCE]
    omitted = checker.requires_dist_values(
        "Metadata-Version: 2.1\nName: atomaestro\nRequires-Python: >=3.10\n"
    )
    requires_txt = checker.requires_txt_as_metadata(
        f"{DIRECT_REFERENCE.replace(' @ ', '@ ')}\n"
        "numpy\n\n[tui]\ntextual\n"
    )
    assert omitted == []
    assert checker.direct_reference_requirements(requires_txt) == [
        DIRECT_REFERENCE.replace(" @ ", "@ ")
    ]
    assert checker.is_release_tag("v0.0.6")
    assert not checker.is_release_tag("v")
    assert not checker.is_release_tag("v1")
    assert not checker.is_release_tag("v1.2")
    assert not checker.is_release_tag("v1.2.3rc1")
    assert not checker.is_release_tag("v1.2.3-rc1")


def test_current_molcrys_requirement_is_a_public_release_range() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"{MOLCRYSKIT_REQUIREMENT}"' in pyproject
    assert "git+https://github.com/SchrodingersCattt/MolCrysKit" not in pyproject


def test_release_jobs_reject_direct_references_and_non_final_tags() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count("reject_direct_dependencies.py") == 3
    assert workflow.count("--project atomaestro") == 3
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
    guard_install = "python -m pip install -c requirements/ci-constraints.txt packaging"
    for job in (publish, release):
        assert "actions/setup-python@v5" in job
        assert guard_install in job
        assert job.index("actions/setup-python@v5") < job.index(guard_install)
        assert job.index(guard_install) < job.index("reject_direct_dependencies.py")
    assert (
        'python -m pip install -c requirements/ci-constraints.txt '
        'build setuptools "setuptools-scm[toml]"'
    ) in build
    assert build.index("setuptools-scm[toml]") < build.index(
        "reject_direct_dependencies.py"
    )


def _write_wheel(path: Path, metadata: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("atomaestro-9.9.9.dist-info/METADATA", metadata)


def _isolated_project_artifacts(tmp_path: Path) -> list[Path]:
    """Build a wheel and sdist from a copy so the source tree stays put."""
    destination = tmp_path / "source"
    shutil.copytree(
        ROOT,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            "dist",
            "build",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.egg-info",
        ),
    )
    output = tmp_path / "dist"
    env = os.environ.copy()
    env["SETUPTOOLS_SCM_PRETEND_VERSION"] = "9.9.9"
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output)],
        cwd=destination,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    artifacts = sorted(output.glob("*.whl")) + sorted(output.glob("*.tar.gz"))
    assert len(artifacts) == 2
    return artifacts


@pytest.mark.timeout(180)
def test_upload_guard_accepts_built_wheel_and_sdist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real release artifacts both pass the same guard used to publish."""
    from packaging.requirements import Requirement

    checker = _checker()
    artifacts = _isolated_project_artifacts(tmp_path)
    assert any(path.name.endswith(".tar.gz") for path in artifacts)
    wheel = next(path for path in artifacts if path.suffix == ".whl")
    label, metadata = checker.artifact_texts(wheel)[0]
    assert label == "METADATA"
    requirements = [Requirement(value) for value in checker.requires_dist_values(metadata)]
    assert Requirement(MOLCRYSKIT_REQUIREMENT) in requirements
    assert all(req.url is None for req in requirements)
    status = checker.main(
        ["--project", "atomaestro", "--tag", "v9.9.9", *map(str, artifacts)]
    )
    assert status == 0
    assert not capsys.readouterr().err

    legal = tmp_path / "legal.whl"
    _write_wheel(
        legal,
        "Metadata-Version: 2.1\n"
        "Name: atomaestro\n"
        "Version: 9.9.9\n"
        "Requires-Dist: molcrys-kit[desktop]>=0.9.0,<0.10.0\n"
        "Requires-Dist: numpy\n",
    )
    assert checker.main(["--tag", "v9.9.9", str(legal)]) == 0
    assert "direct reference" not in capsys.readouterr().err
