"""The matter-vis forwarder installs atomaestro and owns no modules.

The wheels in this test are built locally. Nothing is downloaded from PyPI
and nothing is published. The predecessor wheel is a fixture, not the
public matter-vis 0.0.6 release.
"""

from __future__ import annotations

import email
import importlib.util
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def _distribution_tests():
    spec = importlib.util.spec_from_file_location(
        "distribution_metadata_under_test",
        ROOT / "tests" / "test_distribution_metadata.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

FORWARDER = ROOT / ".github" / "scripts" / "build_matter_vis_forwarder.py"


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_matter_vis_forwarder",
        FORWARDER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metadata(path: Path) -> email.message.Message:
    with zipfile.ZipFile(path) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        assert len(names) == 1
        return email.message_from_bytes(archive.read(names[0]))


def _venv_python(root: Path) -> Path:
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _predecessor(tmp_path: Path) -> Path:
    """A local matter-vis 0.0.6 that owns a mat_viewer module and mat-vis."""
    project = tmp_path / "predecessor"
    package = project / "mat_viewer"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('OWNER = "predecessor"\n', encoding="utf-8")
    (package / "marker.py").write_text(
        "def main() -> None:\n    print('predecessor')\n",
        encoding="utf-8",
    )
    (project / "pyproject.toml").write_text(
        """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "matter-vis"
version = "0.0.6"
description = "Local predecessor fixture. Not the published release."
requires-python = ">=3.10"

[project.scripts]
mat-vis = "mat_viewer.marker:main"

[tool.setuptools.packages.find]
include = ["mat_viewer*"]
""",
        encoding="utf-8",
    )
    output = tmp_path / "predecessor-dist"
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output)],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def _install(python: Path, *wheels: Path, upgrade: bool = False) -> None:
    command = [str(python), "-m", "pip", "install", "--no-deps"]
    if upgrade:
        command.append("--upgrade")
    command.extend(str(wheel) for wheel in wheels)
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=python.parent,
        env=_clean_env(),
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.timeout(180)
def test_forwarder_matches_the_wheel_and_replaces_a_local_predecessor(
    tmp_path: Path,
) -> None:
    distribution = _distribution_tests()
    artifacts = distribution._isolated_project_artifacts(tmp_path / "atomaestro")
    source = next(path for path in artifacts if path.suffix == ".whl")
    source_meta = _metadata(source)
    assert source_meta.get("Name") == "atomaestro"
    assert source_meta.get("Version") == "9.9.9"
    source_extras = set(source_meta.get_all("Provides-Extra") or [])
    assert "web" in source_extras

    builder = _builder()
    with pytest.raises(ValueError, match="dist/"):
        builder.assert_outside_release_dist(ROOT / "dist")
    with pytest.raises(ValueError, match="dist/"):
        builder.assert_outside_release_dist(ROOT / "dist" / "forwarder")

    outdir = tmp_path / "forwarder"
    built = builder.build_forwarder(source, outdir)
    wheel = next(path for path in built if path.suffix == ".whl")
    sdist = next(path for path in built if path.name.endswith(".tar.gz"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert not any("mat_viewer" in name for name in names)
    assert not any(name.endswith(".dist-info/entry_points.txt") for name in names)
    meta = _metadata(wheel)
    assert meta.get("Name") == "matter-vis"
    assert meta.get("Version") == "9.9.9"
    requirements = [Requirement(value) for value in meta.get_all("Requires-Dist") or []]
    base = [item for item in requirements if item.marker is None]
    assert len(base) == 1
    assert base[0].name == "atomaestro"
    assert str(base[0].specifier) == "==9.9.9"
    assert base[0].url is None
    extra_names = set()
    for item in requirements:
        if item.marker is None:
            continue
        assert item.name == "atomaestro"
        assert str(item.specifier) == "==9.9.9"
        assert item.url is None
        matched = [
            extra
            for extra in source_extras
            if item.marker.evaluate({"extra": extra})
            and not item.marker.evaluate({"extra": "not-a-real-extra"})
        ]
        assert len(matched) == 1
        extra_names.add(matched[0])
    assert extra_names == source_extras
    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getnames()
    assert not any("mat_viewer" in name for name in members)

    checker = distribution._checker()
    assert checker.main(["--project", "atomaestro", "--tag", "v9.9.9", str(wheel)]) == 1
    assert checker.main(["--project", "matter-vis", "--tag", "v9.9.9", str(wheel)]) == 0

    env = tmp_path / "env"
    subprocess.run(
        [sys.executable, "-m", "venv", str(env)],
        check=True,
        cwd=tmp_path,
        env=_clean_env(),
    )
    python = _venv_python(env)
    predecessor = _predecessor(tmp_path)
    _install(python, predecessor)
    _install(python, wheel, source, upgrade=True)
    probe = subprocess.run(
        [
            str(python),
            "-c",
            """\
import importlib.metadata as metadata
import sysconfig
from pathlib import Path
forwarder = metadata.distribution("matter-vis")
atomaestro = metadata.distribution("atomaestro")
print("VERSION", forwarder.version)
print("ATO", atomaestro.version)
forward_files = [str(path) for path in forwarder.files or []]
atom_files = [str(path) for path in atomaestro.files or []]
print("FWD_COUNT", len(forward_files))
print("OVERLAP", len(set(forward_files) & set(atom_files)))
print("FWD_CODE", any("mat_viewer" in path for path in forward_files))
print("ATO_CODE", any(path.startswith("mat_viewer/") for path in atom_files))
scripts = [
    item.name
    for item in forwarder.entry_points
    if item.group == "console_scripts"
]
print("FWD_SCRIPTS", ",".join(scripts))
owned = [
    item.name
    for item in atomaestro.entry_points
    if item.group == "console_scripts"
]
print("ATO_SCRIPTS", ",".join(owned))
pure = Path(sysconfig.get_paths()["purelib"])
print("MARKER", (pure / "mat_viewer" / "marker.py").exists())
print("INIT", (pure / "mat_viewer" / "__init__.py").exists())
""",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_clean_env(),
    )
    assert probe.returncode == 0, probe.stderr
    facts = dict(
        line.split(" ", 1)
        for line in probe.stdout.splitlines()
        if " " in line
    )
    assert facts["VERSION"] == "9.9.9"
    assert facts["ATO"] == "9.9.9"
    assert facts["OVERLAP"] == "0"
    assert facts["FWD_CODE"] == "False"
    assert facts["ATO_CODE"] == "True"
    assert facts["FWD_SCRIPTS"] == ""
    assert facts["ATO_SCRIPTS"] == "mat-vis"
    assert facts["MARKER"] == "False"
    assert facts["INIT"] == "True"
    assert int(facts["FWD_COUNT"]) > 0
