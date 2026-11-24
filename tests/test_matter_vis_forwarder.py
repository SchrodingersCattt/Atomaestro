"""Supported migration is a new environment or uninstall, then atomaestro.

An empty matter-vis wheel that depends on atomaestro is not published.
Pip installs that dependency before it removes the old matter-vis record,
and that removal deletes the new files. The release check rejects the
artifact. These wheels are built locally. The predecessor is synthetic
and is not the public 0.0.6 release. Its file list is the public record's
mat_viewer paths, with synthetic contents. ``--no-deps`` is used so these
virtual environments do not fetch the full MolCrysKit desktop stack. It is not the
supported user command.
"""

from __future__ import annotations

import email
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import textwrap
import zipfile
from contextlib import redirect_stderr
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR_INIT = (
    b'OWNER = "predecessor"\n'
    b"\n"
    b"def main() -> None:\n"
    b'    print("predecessor")\n'
)
PUBLIC_RECORD_PATHS = ROOT / "tests" / "fixtures" / "matter_vis_0_0_6_record_paths.txt"
OVERLAP_SAMPLE = "mat_viewer/tui/app.py"
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
try:
    metadata.distribution("matter-vis")
except metadata.PackageNotFoundError:
    matter_vis = False
else:
    raise SystemExit("matter-vis is installed beside atomaestro")
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
            "matter_vis": matter_vis,
            "atomaestro_owns_init": owns(atom_names, "mat_viewer/__init__.py"),
            "atomaestro_owns_script": owns(atom_names, "/mat-vis")
            or owns(atom_names, "/mat-vis.exe"),
            "atomaestro_version": atomaestro.version,
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


def _public_record_paths() -> tuple[str, ...]:
    paths = [
        line.strip()
        for line in PUBLIC_RECORD_PATHS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(paths) == 320
    assert paths[0] == "mat_viewer/__init__.py"
    assert OVERLAP_SAMPLE in paths
    return tuple(paths)


def _predecessor(tmp_path: Path) -> Path:
    """Synthetic matter-vis 0.0.6 with the public record's mat_viewer paths.

    The bytes are not the published wheel. The path list is. The console
    script is included because the public entry point is ``mat-vis`` and
    pip removes console scripts from the installed distribution even when
    the wheel record does not list the generated script.
    """
    project = tmp_path / "predecessor"
    for relative in _public_record_paths():
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative == "mat_viewer/__init__.py":
            target.write_bytes(PREDECESSOR_INIT)
        elif relative.endswith(".py"):
            target.write_text('OWNER = "predecessor"\n', encoding="utf-8")
        else:
            target.write_text("predecessor\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "matter-vis"
version = "0.0.6"
description = "Synthetic predecessor. Not the published release."
requires-python = ">=3.10"

[project.scripts]
mat-vis = "mat_viewer:main"

[tool.setuptools.packages.find]
include = ["mat_viewer*"]

[tool.setuptools.package-data]
mat_viewer = ["**/*"]
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
        env=_build_env(),
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


def _build_env() -> dict[str, str]:
    """Outer interpreter, without the checkout on ``PYTHONPATH``.

    ``build`` is installed for that interpreter. ``PYTHONNOUSERSITE`` would
    hide it. The virtual environments used for install probes set
    ``PYTHONNOUSERSITE`` separately.
    """
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
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


def _make_venv(path: Path, cwd: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "venv", str(path)],
        check=True,
        cwd=cwd,
        env=_clean_env(),
    )
    return _venv_python(path)


def _assert_predecessor(python: Path, cwd: Path) -> None:
    script = _script(python)
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "from mat_viewer import main" in text
    assert "from mat_viewer.cli import main" not in text
    run = _run([str(script)], cwd)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "predecessor\n"
    pure = _purelib(python, cwd)
    init = pure / "mat_viewer" / "__init__.py"
    assert init.read_bytes() == PREDECESSOR_INIT
    sample = pure / OVERLAP_SAMPLE
    assert sample.read_text(encoding="utf-8") == 'OWNER = "predecessor"\n'
    recorded = {
        line.split(",", 1)[0]
        for line in (next(pure.glob("matter_vis-0.0.6.dist-info/RECORD"))).read_text(
            encoding="utf-8"
        ).splitlines()
        if line
    }
    missing = [path for path in _public_record_paths() if path not in recorded]
    assert missing == []


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
    assert reported["matter_vis"] is False
    assert reported["atomaestro_owns_init"] is True
    assert reported["atomaestro_owns_script"] is True
    assert reported["atomaestro_version"] == "9.9.9"
    assert reported["atomaestro_scripts"] == ["mat-vis"]
    return on_disk


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
    refused = io.StringIO()
    with redirect_stderr(refused):
        assert checker.main(["--project", "matter-vis", "--tag", "v9.9.9", str(wheel)]) == 1
    assert "must not be published" in refused.getvalue()
    mismatch = io.StringIO()
    with redirect_stderr(mismatch):
        assert checker.main(["--project", "atomaestro", "--tag", "v9.9.9", str(wheel)]) == 1
    assert "must not be published" in mismatch.getvalue()
    bare = io.StringIO()
    with redirect_stderr(bare):
        assert checker.main(["--tag", "v9.9.9", str(wheel)]) == 1
    assert "must not be published" in bare.getvalue()
    assert builder.main([str(source), "--outdir", str(tmp_path / "refused")]) == 2
    assert not (tmp_path / "refused").exists()

    wheel_init = _wheel_bytes(source, "mat_viewer/__init__.py")
    assert wheel_init != PREDECESSOR_INIT
    assert b"def __getattr__" in wheel_init

    house = _wheelhouse(tmp_path, wheel, source)
    predecessor = _predecessor(tmp_path)
    cwd = tmp_path / "run"
    cwd.mkdir()
    sample = _wheel_bytes(source, OVERLAP_SAMPLE)
    assert b"predecessor" not in sample

    fresh = _make_venv(tmp_path / "fresh", cwd)
    _pip(
        fresh,
        [
            "install",
            "--no-index",
            "--find-links",
            str(house),
            "--no-deps",
            "atomaestro",
        ],
        cwd,
    )
    _assert_real_install(fresh, cwd, wheel_init)
    fresh_pure = _purelib(fresh, cwd)
    assert (fresh_pure / OVERLAP_SAMPLE).read_bytes() == sample
    only_new = _run(
        [
            str(fresh),
            "-c",
            (
                "import importlib.metadata as metadata\n"
                "try:\n"
                "    metadata.distribution('matter-vis')\n"
                "except metadata.PackageNotFoundError:\n"
                "    print('absent')\n"
                "else:\n"
                "    raise SystemExit('matter-vis is installed')\n"
            ),
        ],
        cwd,
    )
    assert only_new.returncode == 0, only_new.stderr
    assert only_new.stdout.strip() == "absent"
    _pip(fresh, ["uninstall", "-y", "atomaestro"], cwd)
    assert not _script(fresh).exists()
    assert not (fresh_pure / "mat_viewer").exists()
    fresh_import = _run([str(fresh), "-c", "import mat_viewer"], cwd)
    assert fresh_import.returncode != 0
    assert "ModuleNotFoundError" in fresh_import.stderr

    migrated = _make_venv(tmp_path / "migrated", cwd)
    _pip(migrated, ["install", "--no-deps", str(predecessor)], cwd)
    _assert_predecessor(migrated, cwd)
    _pip(migrated, ["uninstall", "-y", "matter-vis"], cwd)
    assert not _script(migrated).exists()
    migrated_pure = _purelib(migrated, cwd)
    assert not (migrated_pure / "mat_viewer" / "__init__.py").exists()
    assert not (migrated_pure / OVERLAP_SAMPLE).exists()
    removed_import = _run([str(migrated), "-c", "import mat_viewer"], cwd)
    assert removed_import.returncode != 0
    assert "ModuleNotFoundError" in removed_import.stderr
    _pip(
        migrated,
        [
            "install",
            "--no-index",
            "--find-links",
            str(house),
            "--no-deps",
            "atomaestro",
        ],
        cwd,
    )
    _assert_real_install(migrated, cwd, wheel_init)
    restored = (migrated_pure / OVERLAP_SAMPLE).read_bytes()
    assert restored == sample
    assert b'OWNER = "predecessor"' not in restored

    damaged = _make_venv(tmp_path / "damaged", cwd)
    _pip(damaged, ["install", "--no-deps", str(predecessor)], cwd)
    _pip(
        damaged,
        [
            "install",
            "--no-index",
            "--find-links",
            str(house),
            "--no-deps",
            "atomaestro",
        ],
        cwd,
    )
    _pip(
        damaged,
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
    damaged_pure = _purelib(damaged, cwd)
    assert not _script(damaged).exists()
    assert not (damaged_pure / "mat_viewer" / "__init__.py").exists()
    assert not (damaged_pure / OVERLAP_SAMPLE).exists()
    _pip(damaged, ["uninstall", "-y", "matter-vis", "atomaestro"], cwd)
    _pip(
        damaged,
        [
            "install",
            "--no-index",
            "--find-links",
            str(house),
            "--no-deps",
            "atomaestro",
        ],
        cwd,
    )
    _assert_real_install(damaged, cwd, wheel_init)
    assert (damaged_pure / OVERLAP_SAMPLE).read_bytes() == sample


def _tiny_wheel(
    directory: Path,
    folder: str,
    dist_name: str,
    version: str,
    *,
    body: str,
    depend: str | None,
) -> Path:
    project = directory / folder
    project.mkdir(parents=True, exist_ok=True)
    if dist_name == "atomaestro":
        package = project / "mat_viewer"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(body, encoding="utf-8")
        (package / "cli.py").write_text(
            "def main() -> None:\n    print('atomaestro')\n",
            encoding="utf-8",
        )
        scripts = 'mat-vis = "mat_viewer.cli:main"\n'
        packages = 'include = ["mat_viewer*"]\n'
        tool = "[tool.setuptools.packages.find]\n" + packages
    elif depend is None:
        package = project / "mat_viewer"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(body, encoding="utf-8")
        scripts = 'mat-vis = "mat_viewer:main"\n'
        tool = "[tool.setuptools.packages.find]\ninclude = [\"mat_viewer*\"]\n"
    else:
        scripts = ""
        tool = "[tool.setuptools]\npackages = []\npy-modules = []\n"
    dependency = ""
    if depend is not None:
        dependency = f"dependencies = [{depend!r}]\n"
    script_table = f"[project.scripts]\n{scripts}" if scripts else ""
    (project / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [build-system]
            requires = ["setuptools>=68"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "{dist_name}"
            version = "{version}"
            requires-python = ">=3.10"
            {dependency}
            {script_table}
            {tool}
            """
        ),
        encoding="utf-8",
    )
    output = directory / f"{folder}-dist"
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output)],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        env=_build_env(),
    )
    assert completed.returncode == 0, completed.stderr
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


@pytest.mark.timeout(180)
@pytest.mark.parametrize("pip_version", ["24.0", "26.3"])
def test_recorded_pip_versions_delete_overlap_until_reinstall(
    tmp_path: Path,
    pip_version: str,
) -> None:
    """Evidence for two pip releases. This does not cover the versions between them."""
    body = 'OWNER = "predecessor"\n\ndef main() -> None:\n    print("predecessor")\n'
    atom_body = 'OWNER = "atomaestro"\n'
    predecessor = _tiny_wheel(
        tmp_path, "pred", "matter-vis", "0.0.6", body=body, depend=None
    )
    atomaestro = _tiny_wheel(
        tmp_path, "atom", "atomaestro", "9.9.9", body=atom_body, depend=None
    )
    forwarder = _tiny_wheel(
        tmp_path,
        "fwd",
        "matter-vis",
        "9.9.9",
        body="",
        depend="atomaestro==9.9.9",
    )
    house = tmp_path / f"house-{pip_version}"
    house.mkdir()
    shutil.copy(atomaestro, house / atomaestro.name)
    shutil.copy(forwarder, house / forwarder.name)
    cwd = tmp_path / f"cwd-{pip_version}"
    cwd.mkdir()
    python = _make_venv(tmp_path / f"venv-{pip_version}", cwd)
    _pip(python, ["install", "--disable-pip-version-check", f"pip=={pip_version}"], cwd)
    _pip(python, ["install", "--no-deps", str(predecessor)], cwd)
    before = _run([str(_script(python))], cwd)
    assert before.returncode == 0, before.stderr
    assert before.stdout == "predecessor\n"
    _pip(python, ["install", "--no-deps", str(atomaestro)], cwd)
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
        ],
        cwd,
    )
    pure = _purelib(python, cwd)
    assert not _script(python).exists()
    assert not (pure / "mat_viewer" / "__init__.py").exists()
    _pip(python, ["uninstall", "-y", "matter-vis", "atomaestro"], cwd)
    _pip(python, ["install", "--no-deps", str(atomaestro)], cwd)
    after = _run([str(_script(python))], cwd)
    assert after.returncode == 0, after.stderr
    assert after.stdout == "atomaestro\n"
    assert (pure / "mat_viewer" / "__init__.py").read_text(encoding="utf-8") == atom_body
