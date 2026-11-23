"""The matter-vis forwarder installs atomaestro and owns no modules.

The wheels in this test are built locally. Nothing is downloaded from PyPI
and nothing is published. The predecessor wheel is a fixture, not the
public matter-vis 0.0.6 release.

``--no-deps`` keeps ``matter-vis`` ahead of ``atomaestro`` in one
transaction and skips the MolCrysKit git pin. Installing ``atomaestro``
first deletes the paths the predecessor recorded, including
``mat_viewer/__init__.py`` and ``mat-vis``. This test does not fetch the
pin and does not download public matter-vis 0.0.6.
"""

from __future__ import annotations

import email
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR_INIT = b'OWNER = "predecessor"\n'
_COMMANDS = (
    "render",
    "inspect",
    "capabilities",
    "skills",
    "serve",
    "tui",
    "operate",
)
_INSTALLED_PROBE = r"""
import hashlib
import importlib
import importlib.metadata as metadata
import json
import sysconfig
from pathlib import Path

import mat_viewer

path = Path(mat_viewer.__file__).resolve()
data = path.read_bytes()
try:
    importlib.import_module("mat_viewer.marker")
except ModuleNotFoundError:
    marker = False
else:
    raise SystemExit("predecessor marker is still importable")
forwarder = metadata.distribution("matter-vis")
atomaestro = metadata.distribution("atomaestro")
payload = mat_viewer.capabilities()


def names(dist):
    return [str(item).replace("\\", "/") for item in dist.files or []]


def resolved(dist):
    found = []
    for item in dist.files or []:
        found.append(str(Path(dist.locate_file(item)).resolve()))
    return found


def owns(paths, suffix):
    return any(item.endswith(suffix) for item in paths)


forward_names = names(forwarder)
atom_names = names(atomaestro)
print(
    json.dumps(
        {
            "file": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "schema": payload["schema"],
            "distribution": payload["distribution"],
            "version": payload["version"],
            "marker_importable": marker,
            "overlap": sorted(set(resolved(forwarder)) & set(resolved(atomaestro))),
            "forwarder_code": any("mat_viewer" in item for item in forward_names),
            "atomaestro_owns_init": owns(atom_names, "mat_viewer/__init__.py"),
            "forwarder_owns_script": owns(forward_names, "/mat-vis")
            or owns(forward_names, "/mat-vis.exe"),
            "atomaestro_owns_script": owns(atom_names, "/mat-vis")
            or owns(atom_names, "/mat-vis.exe"),
            "forwarder_version": forwarder.version,
            "atomaestro_version": atomaestro.version,
            "forwarder_scripts": [
                item.name
                for item in forwarder.entry_points
                if item.group == "console_scripts"
            ],
            "atomaestro_scripts": [
                item.name
                for item in atomaestro.entry_points
                if item.group == "console_scripts"
            ],
        }
    )
)
"""


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
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=_clean_env(),
    )


def _pip(python: Path, args: list[str], cwd: Path) -> None:
    completed = _run([str(python), "-m", "pip", *args], cwd)
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _script(python: Path) -> Path:
    name = "mat-vis.exe" if os.name == "nt" else "mat-vis"
    return python.parent / name


def _purelib(python: Path, cwd: Path) -> Path:
    completed = _run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        cwd,
    )
    assert completed.returncode == 0, completed.stderr
    return Path(completed.stdout.strip())


def _wheel_bytes(wheel: Path, name: str) -> bytes:
    with zipfile.ZipFile(wheel) as archive:
        assert name in archive.namelist()
        return archive.read(name)


def _wheelhouse(directory: Path, *wheels: Path) -> Path:
    house = directory / "wheelhouse"
    house.mkdir()
    for wheel in wheels:
        shutil.copy(wheel, house / wheel.name)
    return house


def _upgrade(python: Path, house: Path, cwd: Path) -> None:
    """Upgrade matter-vis before installing atomaestro.

    Listing atomaestro first makes pip remove the new modules while it
    deletes the predecessor record. ``--no-deps`` keeps that command order
    and skips the git pin.
    """
    _pip(
        python,
        [
            "install",
            "--upgrade",
            "--no-index",
            "--find-links",
            str(house),
            "--no-deps",
            "matter-vis",
            "atomaestro",
        ],
        cwd,
    )


def _assert_predecessor(python: Path, cwd: Path) -> None:
    script = _script(python)
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "from mat_viewer.marker import main" in text
    assert "from mat_viewer.cli import main" not in text
    run = _run([str(script)], cwd)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "predecessor\n"
    init = _purelib(python, cwd) / "mat_viewer" / "__init__.py"
    assert init.read_bytes() == PREDECESSOR_INIT


def _assert_real_install(python: Path, cwd: Path, wheel_init: bytes) -> bytes:
    script = _script(python)
    text = script.read_text(encoding="utf-8")
    assert "from mat_viewer.cli import main" in text
    assert "mat_viewer.marker" not in text
    help_run = _run([str(script), "--help"], cwd)
    assert help_run.returncode == 0, help_run.stderr
    assert "predecessor" not in help_run.stdout
    assert "MatterVis: publication-quality crystal structure visualization." in help_run.stdout
    for name in _COMMANDS:
        assert name in help_run.stdout
    capabilities = _run([str(script), "capabilities", "--json"], cwd)
    assert capabilities.returncode == 0, capabilities.stderr
    assert "predecessor" not in capabilities.stdout
    payload = json.loads(capabilities.stdout)
    assert payload["schema"] == "mattervis.capabilities/v1"
    assert payload["distribution"] == "atomaestro"
    assert payload["version"] == "9.9.9"
    probe = _run([str(python), "-c", _INSTALLED_PROBE], cwd)
    assert probe.returncode == 0, probe.stderr
    reported = json.loads(probe.stdout)
    installed = Path(reported["file"])
    pure = _purelib(python, cwd)
    assert installed == (pure / "mat_viewer" / "__init__.py").resolve()
    assert not installed.is_relative_to(ROOT.resolve())
    on_disk = installed.read_bytes()
    assert hashlib.sha256(on_disk).hexdigest() == reported["sha256"]
    assert on_disk == wheel_init
    assert on_disk != PREDECESSOR_INIT
    assert reported["schema"] == "mattervis.capabilities/v1"
    assert reported["distribution"] == "atomaestro"
    assert reported["version"] == "9.9.9"
    assert reported["marker_importable"] is False
    assert not (pure / "mat_viewer" / "marker.py").exists()
    assert reported["overlap"] == []
    assert reported["forwarder_code"] is False
    assert reported["atomaestro_owns_init"] is True
    assert reported["forwarder_owns_script"] is False
    assert reported["atomaestro_owns_script"] is True
    assert reported["forwarder_version"] == "9.9.9"
    assert reported["atomaestro_version"] == "9.9.9"
    assert reported["forwarder_scripts"] == []
    assert reported["atomaestro_scripts"] == ["mat-vis"]
    return on_disk


def _assert_library_removed(python: Path, cwd: Path) -> None:
    assert not _script(python).exists()
    pure = _purelib(python, cwd)
    assert not (pure / "mat_viewer").exists()
    imported = _run([str(python), "-c", "import mat_viewer"], cwd)
    assert imported.returncode != 0
    assert "ModuleNotFoundError" in imported.stderr
    assert "mat_viewer" in imported.stderr
    probe = _run(
        [
            str(python),
            "-c",
            """
import importlib.metadata as metadata
import json
forwarder = metadata.distribution("matter-vis")
try:
    metadata.distribution("atomaestro")
except metadata.PackageNotFoundError:
    present = False
else:
    present = True
names = [str(item).replace("\\\\", "/") for item in forwarder.files or []]
print(json.dumps({
    "forwarder": forwarder.version,
    "atomaestro": present,
    "code": any("mat_viewer" in item for item in names),
}))
""",
        ],
        cwd,
    )
    assert probe.returncode == 0, probe.stderr
    reported = json.loads(probe.stdout)
    assert reported == {"forwarder": "9.9.9", "atomaestro": False, "code": False}


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

    wheel_init = _wheel_bytes(source, "mat_viewer/__init__.py")
    assert wheel_init != PREDECESSOR_INIT
    assert b"def __getattr__" in wheel_init
    house = _wheelhouse(tmp_path, wheel, source)
    predecessor = _predecessor(tmp_path)
    cwd = tmp_path / "run"
    cwd.mkdir()

    kept = tmp_path / "kept"
    python = _venv_python(kept)
    subprocess.run(
        [sys.executable, "-m", "venv", str(kept)],
        check=True,
        cwd=cwd,
        env=_clean_env(),
    )
    _pip(python, ["install", "--no-deps", str(predecessor)], cwd)
    _assert_predecessor(python, cwd)
    _upgrade(python, house, cwd)
    installed = _assert_real_install(python, cwd, wheel_init)
    _pip(python, ["uninstall", "-y", "matter-vis"], cwd)
    assert _script(python).read_text(encoding="utf-8").count("from mat_viewer.cli import main") == 1
    capabilities = _run([str(_script(python)), "capabilities", "--json"], cwd)
    assert capabilities.returncode == 0, capabilities.stderr
    assert json.loads(capabilities.stdout)["version"] == "9.9.9"
    remaining = _run(
        [
            str(python),
            "-c",
            """
import hashlib, importlib.metadata as metadata, json
from pathlib import Path
import mat_viewer
path = Path(mat_viewer.__file__).resolve()
try:
    metadata.distribution("matter-vis")
except metadata.PackageNotFoundError:
    present = False
else:
    present = True
payload = mat_viewer.capabilities()
print(json.dumps({
    "file": str(path),
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "matter_vis": present,
    "version": payload["version"],
    "distribution": payload["distribution"],
    "atomaestro": metadata.distribution("atomaestro").version,
}))
""",
        ],
        cwd,
    )
    assert remaining.returncode == 0, remaining.stderr
    after_uninstall = json.loads(remaining.stdout)
    assert after_uninstall["matter_vis"] is False
    assert after_uninstall["atomaestro"] == "9.9.9"
    assert after_uninstall["version"] == "9.9.9"
    assert after_uninstall["distribution"] == "atomaestro"
    reread = Path(after_uninstall["file"])
    assert reread == (_purelib(python, cwd) / "mat_viewer" / "__init__.py").resolve()
    assert reread.read_bytes() == installed
    assert hashlib.sha256(reread.read_bytes()).hexdigest() == after_uninstall["sha256"]

    removed = tmp_path / "removed"
    other = _venv_python(removed)
    subprocess.run(
        [sys.executable, "-m", "venv", str(removed)],
        check=True,
        cwd=cwd,
        env=_clean_env(),
    )
    _pip(other, ["install", "--no-deps", str(predecessor)], cwd)
    _upgrade(other, house, cwd)
    _assert_real_install(other, cwd, wheel_init)
    _pip(other, ["uninstall", "-y", "atomaestro"], cwd)
    _assert_library_removed(other, cwd)

    deleted = tmp_path / "deleted"
    early = _venv_python(deleted)
    subprocess.run(
        [sys.executable, "-m", "venv", str(deleted)],
        check=True,
        cwd=cwd,
        env=_clean_env(),
    )
    _pip(early, ["install", "--no-deps", str(predecessor)], cwd)
    _pip(
        early,
        ["install", "--no-index", "--find-links", str(house), "--no-deps", "atomaestro"],
        cwd,
    )
    _pip(
        early,
        [
            "install",
            "--upgrade",
            "--no-index",
            "--find-links",
            str(house),
            "--no-deps",
            "matter-vis",
        ],
        cwd,
    )
    pure = _purelib(early, cwd)
    assert not _script(early).exists()
    assert not (pure / "mat_viewer" / "__init__.py").exists()
    assert (pure / "mat_viewer" / "cli.py").is_file()
    damaged = _run(
        [
            str(early),
            "-c",
            """
import json
import mat_viewer
init_api = hasattr(mat_viewer, "capabilities")
try:
    import mat_viewer.cli
except ModuleNotFoundError:
    cli = False
else:
    cli = True
print(json.dumps({
    "file": mat_viewer.__file__,
    "init_api": init_api,
    "cli": cli,
}))
""",
        ],
        cwd,
    )
    assert damaged.returncode == 0, damaged.stderr
    assert json.loads(damaged.stdout) == {
        "file": None,
        "init_api": False,
        "cli": True,
    }
    recorded = _run(
        [
            str(early),
            "-c",
            """
import importlib.metadata as metadata, json
forwarder = metadata.distribution("matter-vis")
atomaestro = metadata.distribution("atomaestro")
names = [str(item).replace("\\\\", "/") for item in forwarder.files or []]
print(json.dumps({
    "forwarder": forwarder.version,
    "atomaestro": atomaestro.version,
    "code": any("mat_viewer" in item for item in names),
}))
""",
        ],
        cwd,
    )
    assert recorded.returncode == 0, recorded.stderr
    assert json.loads(recorded.stdout) == {
        "forwarder": "9.9.9",
        "atomaestro": "9.9.9",
        "code": False,
    }
