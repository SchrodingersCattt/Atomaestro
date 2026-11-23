"""Build an empty matter-vis wheel that depends on one atomaestro wheel.

The command-line entry point refuses to write that wheel. Pip installs the
dependency before it removes an older matter-vis record, and that removal
deletes ``mat_viewer`` and ``mat-vis``. The release check rejects the
artifact. ``build_forwarder`` remains for the test that proves the refusal.

``atomaestro`` keeps ``mat_viewer`` and ``mat-vis``. The supported move is
a new environment, or ``pip uninstall`` of matter-vis, then atomaestro.
"""

from __future__ import annotations

import argparse
import email
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_EXTRA_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def wheel_metadata(path: Path) -> email.message.Message:
    """Return the single METADATA message from an ``atomaestro`` wheel."""
    with zipfile.ZipFile(path) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        if len(names) != 1:
            raise ValueError(f"{path} does not contain one METADATA file")
        return email.message_from_bytes(archive.read(names[0]))


def _final_version(raw: str) -> str:
    from packaging.version import Version

    version = Version(raw)
    if (
        version.epoch != 0
        or version.is_prerelease
        or version.is_devrelease
        or version.is_postrelease
        or version.local is not None
        or str(version) != raw
    ):
        raise ValueError(
            f"{raw!r} is not a final X.Y.Z version. The forwarder pin has "
            "to match a release version, and no release version is chosen "
            "by this script."
        )
    return raw


def _extras(message: email.message.Message) -> tuple[str, ...]:
    names = []
    for raw in message.get_all("Provides-Extra") or []:
        name = raw.strip()
        if not _EXTRA_NAME.fullmatch(name):
            raise ValueError(f"unexpected extra name {raw!r}")
        if name not in names:
            names.append(name)
    return tuple(names)


def render_project(version: str, extras: tuple[str, ...]) -> str:
    """Return the forwarder ``pyproject.toml`` text."""
    lines = [
        "[build-system]",
        'requires = ["setuptools>=68"]',
        'build-backend = "setuptools.build_meta"',
        "",
        "[project]",
        'name = "matter-vis"',
        f'version = "{version}"',
        (
            'description = "Compatibility forwarder. Installs atomaestro '
            'and ships no modules or scripts."'
        ),
        'readme = "README.md"',
        'requires-python = ">=3.10"',
        'license = {text = "MIT"}',
        "dependencies = [",
        f'    "atomaestro=={version}",',
        "]",
        "",
        "[project.optional-dependencies]",
    ]
    for extra in extras:
        key = extra if _EXTRA_NAME.fullmatch(extra) and "-" not in extra else f'"{extra}"'
        lines.append(f'{key} = ["atomaestro[{extra}]=={version}"]')
    lines.extend(
        [
            "",
            "[tool.setuptools]",
            "packages = []",
            "py-modules = []",
            "",
        ]
    )
    return "\n".join(lines)


def render_readme(version: str) -> str:
    return (
        "# matter-vis compatibility forwarder\n\n"
        f"This distribution depends on `atomaestro=={version}` and ships no "
        "Python modules and no console scripts. `atomaestro` owns "
        "`mat_viewer` and the `mat-vis` command.\n\n"
        "Do not upload this wheel. Pip installs atomaestro before it "
        "removes an older matter-vis record, and that removal deletes "
        "mat_viewer and mat-vis. Use a new environment, or uninstall "
        "matter-vis, then install atomaestro.\n"
    )


def assert_outside_release_dist(outdir: Path, repo_root: Path = REPO_ROOT) -> None:
    """Reject the directory the release workflow uploads."""
    resolved = outdir.resolve()
    release_dist = (repo_root / "dist").resolve()
    if resolved == release_dist or release_dist in resolved.parents:
        raise ValueError(
            "Refusing to write the matter-vis forwarder into dist/. "
            "The release workflow uploads every file there as atomaestro."
        )


def build_forwarder(wheel: Path, outdir: Path) -> list[Path]:
    """Build the wheel and sdist for ``wheel`` into ``outdir``."""
    assert_outside_release_dist(outdir)
    message = wheel_metadata(wheel)
    name = (message.get("Name") or "").strip()
    if name != "atomaestro":
        raise ValueError(f"{wheel} is {name or 'unnamed'}, not atomaestro")
    version = _final_version((message.get("Version") or "").strip())
    extras = _extras(message)
    outdir.mkdir(parents=True, exist_ok=True)
    staging = outdir / "_source"
    if staging.exists():
        raise ValueError(f"{staging} already exists")
    staging.mkdir()
    (staging / "pyproject.toml").write_text(
        render_project(version, extras),
        encoding="utf-8",
    )
    (staging / "README.md").write_text(render_readme(version), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(outdir)],
        cwd=staging,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    artifacts = sorted(outdir.glob("*.whl")) + sorted(outdir.glob("*.tar.gz"))
    if len(artifacts) != 2:
        raise RuntimeError(f"expected one wheel and one sdist, found {artifacts}")
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="Built atomaestro wheel.")
    parser.add_argument(
        "--outdir",
        type=Path,
        required=True,
        help="Unused. The command refuses to write a matter-vis wheel.",
    )
    parser.parse_args(argv)
    print(
        "Refusing to build a matter-vis wheel. An empty forwarder is not "
        "a supported upgrade: pip installs atomaestro and then deletes "
        "the files recorded by the installed matter-vis. Uninstall "
        "matter-vis, or use a new environment, then install atomaestro. "
        "The release check rejects a matter-vis artifact.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
