"""Reject a PyPI upload whose metadata uses a direct dependency URL.

PyPI returns HTTP 400 ``Can't have direct dependency`` for a Requires-Dist
entry of the form ``name @ url``. That rule is PEP 440 for public indexes
and is implemented in ``warehouse.forklift.metadata``. A wheel can build in
CI and still be rejected at upload time.

The script also rejects an artifact named ``matter-vis``. This repository
does not publish that name. An upgrade from an installed ``matter-vis``
deletes files written by ``atomaestro``.
"""

from __future__ import annotations

import argparse
import email
import sys
import tarfile
import zipfile
from pathlib import Path

RELEASE_TAG_RE = r"^v[0-9]+\.[0-9]+\.[0-9]+$"


def is_release_tag(tag: str) -> bool:
    """True only for a final ``vX.Y.Z`` tag."""
    import re

    return re.fullmatch(RELEASE_TAG_RE, tag) is not None


def requires_dist_values(metadata_text: str) -> list[str]:
    """Return every Requires-Dist header value, in file order."""
    message = email.message_from_string(metadata_text)
    return list(message.get_all("Requires-Dist") or [])


def distribution_name(metadata_text: str) -> str:
    """Return the canonical Name header, or an empty string when it is absent."""
    message = email.message_from_string(metadata_text)
    return (message.get("Name") or "").strip()


def direct_reference_requirements(metadata_text: str) -> list[str]:
    """Return Requires-Dist values that carry a PEP 440 direct URL."""
    from packaging.requirements import Requirement

    blocked: list[str] = []
    for value in requires_dist_values(metadata_text):
        if Requirement(value).url is not None:
            blocked.append(value)
    return blocked


def requires_txt_as_metadata(text: str) -> str:
    """Turn an egg-info ``requires.txt`` into metadata the same parser can read."""
    lines = ["Metadata-Version: 2.1"]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "[")):
            continue
        lines.append(f"Requires-Dist: {line}")
    return "\n".join(lines) + "\n"


def artifact_texts(path: Path) -> list[tuple[str, str]]:
    """Return labeled metadata bodies for a wheel or sdist.

    The sdist's canonical body is its top-level PKG-INFO. ``requires.txt``
    is included because setuptools can omit every Requires-Dist from
    PKG-INFO while still recording a direct URL there.
    """
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(names) != 1:
                raise ValueError(f"{path} does not contain one METADATA file")
            return [("METADATA", archive.read(names[0]).decode("utf-8"))]
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.isfile()
            ]
            canonical = [
                member
                for member in members
                if member.name.count("/") == 1 and member.name.endswith("/PKG-INFO")
            ]
            if len(canonical) != 1:
                raise ValueError(f"{path} does not contain one top-level PKG-INFO file")
            extracted = archive.extractfile(canonical[0])
            if extracted is None:
                raise ValueError(f"{path} PKG-INFO could not be read")
            bodies = [("PKG-INFO", extracted.read().decode("utf-8"))]
            for member in members:
                if member.name.endswith(".egg-info/requires.txt"):
                    payload = archive.extractfile(member)
                    if payload is None:
                        raise ValueError(f"{path} requires.txt could not be read")
                    bodies.append(
                        ("requires.txt", requires_txt_as_metadata(payload.read().decode("utf-8")))
                    )
            return bodies
    raise ValueError(f"unsupported distribution: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        help="Release tag. When set, it must be a final vX.Y.Z tag.",
    )
    parser.add_argument(
        "--project",
        help=(
            "Distribution name. The release workflow passes atomaestro. "
            "An artifact named matter-vis is rejected even when this "
            "option is omitted or matches that name."
        ),
    )
    parser.add_argument(
        "artifacts",
        nargs="+",
        type=Path,
        help="Wheel or sdist paths to inspect.",
    )
    args = parser.parse_args(argv)
    if args.tag is not None and not is_release_tag(args.tag):
        print(
            f"Release tag must match {RELEASE_TAG_RE}: {args.tag}",
            file=sys.stderr,
        )
        return 1
    failed = False
    saw_direct_reference = False
    for path in args.artifacts:
        try:
            bodies = artifact_texts(path)
        except (OSError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            failed = True
            continue
        canonical = next(
            (text for label, text in bodies if label in {"METADATA", "PKG-INFO"}),
            "",
        )
        from packaging.utils import canonicalize_name

        actual = distribution_name(canonical)
        try:
            normalized = canonicalize_name(actual) if actual else ""
        except ValueError:
            normalized = ""
        if normalized == "matter-vis":
            failed = True
            print(
                f"{path} must not be published. Upgrading an installed "
                "matter-vis deletes files atomaestro has written, including "
                "mat_viewer and mat-vis. Install atomaestro in a new "
                "environment, or uninstall matter-vis first.",
                file=sys.stderr,
            )
        if args.project is not None and normalized != canonicalize_name(args.project):
            failed = True
            print(
                f"{path} cannot be uploaded as {args.project}; "
                f"its name is {actual or 'missing'}.",
                file=sys.stderr,
            )
        if not requires_dist_values(canonical):
            failed = True
            print(
                f"{path} cannot be uploaded to PyPI; its canonical metadata "
                "lists no Requires-Dist requirements.",
                file=sys.stderr,
            )
        for label, text in bodies:
            blocked = direct_reference_requirements(text)
            if not blocked:
                continue
            failed = True
            saw_direct_reference = True
            print(
                f"{path} cannot be uploaded to PyPI; {label} uses a "
                "direct reference:",
                file=sys.stderr,
            )
            for value in blocked:
                print(f"  {value}", file=sys.stderr)
    if saw_direct_reference:
        print(
            "PyPI rejects these dependencies. Replace them with a normal "
            "version requirement before publishing.",
            file=sys.stderr,
        )
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
