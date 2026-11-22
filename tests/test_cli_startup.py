"""Help and lightweight commands must not import the scientific stack.

The assertions are about which modules a fresh process loads and about the
help text those processes print. They do not use a wall-clock threshold.
"""

from __future__ import annotations

import os
import subprocess
import sys

HEAVY = (
    "numpy",
    "ase",
    "plotly",
    "dash",
    "textual",
    "mat_viewer.render",
    "mat_viewer.tui",
    "mat_viewer.properties",
    "mat_viewer.app",
)

_PROBE = """
import os
import sys

from mat_viewer.cli import main

code = 0
try:
    main(sys.argv[1:])
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
watched = os.environ["MAT_VIS_WATCH"].split()
loaded = [name for name in watched if name in sys.modules]
sys.stderr.write("LOADED " + ",".join(loaded) + "\\n")
raise SystemExit(code)
"""

ROOT_HELP = """\
usage: mat-vis [-h] {render,inspect,capabilities,skills,serve,tui,operate} ...

MatterVis: publication-quality crystal structure visualization.

positional arguments:
  {render,inspect,capabilities,skills,serve,tui,operate}
    render              Render an atomistic structure or trajectory.
    inspect             Inspect a structure without rendering it.
    capabilities        Report installed rendering capabilities and exact
                        install hints.
    skills              Discover and read bundled Agent Skills.
    serve               Launch the interactive Dash browser viewer.
    tui                 Terminal-based crystal structure viewer.
    operate             Commit a structural operation.

options:
  -h, --help            show this help message and exit
"""

UNKNOWN_ERROR = (
    "usage: mat-vis [-h] {render,inspect,capabilities,skills,serve,tui,operate} ...\n"
    "mat-vis: error: argument command: invalid choice: 'nope' "
    "(choose from 'render', 'inspect', 'capabilities', 'skills', 'serve', 'tui', 'operate')\n"
)

DASHDASH_ERROR = UNKNOWN_ERROR.replace("'nope'", "'--'")


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MAT_VIS_WATCH"] = " ".join(HEAVY)
    env["COLUMNS"] = "80"
    return subprocess.run(
        [sys.executable, "-c", _PROBE, *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _loaded(result: subprocess.CompletedProcess[str]) -> set[str]:
    lines = [line for line in result.stderr.splitlines() if line.startswith("LOADED ")]
    assert lines, result.stderr
    payload = lines[-1].removeprefix("LOADED ").strip()
    return set(filter(None, payload.split(",")))


def _cli_stderr(result: subprocess.CompletedProcess[str]) -> str:
    kept = [
        line
        for line in result.stderr.splitlines(keepends=True)
        if not line.startswith("LOADED ")
    ]
    return "".join(kept)


def test_root_help_lists_every_command_without_heavy_imports() -> None:
    result = _run(["--help"])
    assert result.returncode == 0
    assert result.stdout == ROOT_HELP
    assert _loaded(result) == set()


def test_lightweight_help_does_not_import_the_scientific_stack() -> None:
    cases = (
        ["capabilities", "--help"],
        ["inspect", "--help"],
        ["serve", "--help"],
        ["tui", "--help"],
        ["operate", "--help"],
        ["skills", "--help"],
    )
    for args in cases:
        result = _run(args)
        assert result.returncode == 0, (args, result.stderr)
        assert _loaded(result) == set(), (args, _loaded(result))


def test_subcommand_help_keeps_its_flags() -> None:
    expectations = {
        ("inspect", "--help"): "--color-by",
        ("serve", "--help"): "--property-data",
        ("tui", "--help"): "--bond-scale",
        ("operate", "--help"): "build-supercell",
        ("capabilities", "--help"): "--require",
        ("skills", "--help"): "usage:",
    }
    for args, needle in expectations.items():
        result = _run(list(args))
        assert result.returncode == 0, result.stderr
        assert needle in result.stdout


def test_capabilities_command_checks_its_contract_without_the_renderer() -> None:
    result = _run(["capabilities"])
    assert result.returncode == 0, result.stderr
    loaded = _loaded(result)
    assert "mat_viewer.render" not in loaded
    assert "mat_viewer.tui" not in loaded


def test_render_help_still_loads_the_render_parser() -> None:
    result = _run(["render", "--help"])
    assert result.returncode == 0
    assert "usage: mat-vis render" in result.stdout
    assert "--backend" in result.stdout
    loaded = _loaded(result)
    assert "mat_viewer.render" in loaded
    assert "numpy" in loaded


def test_unknown_command_and_double_dash_keep_their_errors() -> None:
    unknown = _run(["nope"])
    assert unknown.returncode == 2
    assert _cli_stderr(unknown) == UNKNOWN_ERROR
    assert _loaded(unknown) == set()

    dashed = _run(["--", "render", "--help"])
    assert dashed.returncode == 2
    assert _cli_stderr(dashed) == DASHDASH_ERROR
    assert _loaded(dashed) == set()

    version = _run(["--version"])
    assert version.returncode == 2
    assert "unrecognized arguments: --version" in version.stderr
    assert _loaded(version) == set()
