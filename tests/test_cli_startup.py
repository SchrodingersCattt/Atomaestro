"""Help and lightweight commands must not import the scientific stack.

The assertions are about which modules a fresh process loads and about the
help text those processes print. They do not use a wall-clock threshold.
"""

from __future__ import annotations

import difflib
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

def _without_choice_quotes(text: str) -> str:
    """Drop quotes around argparse choices.

    Python 3.12.16 prints ``choose from render, inspect`` while 3.10, 3.11,
    and earlier 3.12 prints ``choose from 'render', 'inspect'``. The command
    names and the rejected token stay the same.
    """
    return text.replace("'", "")


UNKNOWN_ERROR = (
    "usage: mat-vis [-h] {render,inspect,capabilities,skills,serve,tui,operate} ...\n"
    "mat-vis: error: argument command: invalid choice: nope "
    "(choose from render, inspect, capabilities, skills, serve, tui, operate)\n"
)

_COMMANDS = (
    "render",
    "inspect",
    "capabilities",
    "skills",
    "serve",
    "tui",
    "operate",
)

_ORACLE = r"""
import argparse
import contextlib
import io
import sys

from mat_viewer.cli import (
    _build_capabilities_parser,
    _build_inspect_parser,
    _build_operate_parser,
    _build_render_parser,
    _build_serve_parser,
    _build_tui_parser,
    main,
)

def _exit_code(exc: SystemExit) -> int:
    if exc.code is None:
        return 0
    if isinstance(exc.code, int):
        return exc.code
    return 1


def _capture(func) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            func()
    except SystemExit as exc:
        if not isinstance(exc.code, (int, type(None))):
            stderr.write(str(exc.code) + "\n")
        return _exit_code(exc), stdout.getvalue(), stderr.getvalue()
    return 0, stdout.getvalue(), stderr.getvalue()


def _full_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mat-vis",
        description="MatterVis: publication-quality crystal structure visualization.",
    )
    subparsers = parser.add_subparsers(dest="command")
    _build_render_parser(subparsers)
    _build_inspect_parser(subparsers)
    _build_capabilities_parser(subparsers)
    subparsers.add_parser(
        "skills",
        help="Discover and read bundled Agent Skills.",
    )
    _build_serve_parser(subparsers)
    _build_tui_parser(subparsers)
    _build_operate_parser(subparsers)
    return parser


def _oracle(argv: list[str]) -> tuple[int, str, str]:
    parser = _full_parser()

    def run() -> None:
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help()
            raise SystemExit(0)
        raise SystemExit(f"oracle dispatched {args.command}")

    return _capture(run)


def _actual(argv: list[str]) -> tuple[int, str, str]:
    return _capture(lambda: main(list(argv)))


mode = sys.argv[1]
argv = sys.argv[2:]
if mode == "oracle":
    code, stdout, stderr = _oracle(argv)
elif mode == "actual":
    code, stdout, stderr = _actual(argv)
else:
    raise SystemExit(f"unknown mode {mode}")
sys.stdout.write(f"{code}\n")
sys.stdout.write(stdout)
sys.stderr.write(stderr)
"""


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
    assert _without_choice_quotes(_cli_stderr(unknown)) == UNKNOWN_ERROR
    assert _loaded(unknown) == set()

    version = _run(["--version"])
    assert version.returncode == 2
    assert "unrecognized arguments: --version" in version.stderr
    assert _loaded(version) == set()


def _parser_outcome(mode: str, args: list[str]) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["COLUMNS"] = "80"
    result = subprocess.run(
        [sys.executable, "-c", _ORACLE, mode, *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (mode, args, result.returncode, result.stderr)
    first, _, rest = result.stdout.partition("\n")
    return int(first), rest, result.stderr


def test_leading_double_dash_matches_the_fully_built_parser() -> None:
    """A leading ``--`` must match every parser built on this interpreter.

    Python 3.12.16 skips ``--`` and then selects the next command. Earlier
    releases report ``--`` as an invalid choice. The baseline is that full
    parser, not a second accepted result. A successful command help must be
    the real parser text, so the old summary-only stub cannot pass.
    """
    cases = (
        ["render", "--help"],
        ["inspect", "--help"],
        ["--", "render", "--help"],
        ["--", "inspect", "--help"],
        ["--"],
        ["--", "not-a-command"],
    )
    markers = {"render": "--backend", "inspect": "--color-by"}
    for args in cases:
        oracle = _parser_outcome("oracle", args)
        actual = _parser_outcome("actual", args)
        if actual != oracle:
            diff = "".join(
                difflib.unified_diff(
                    [*_outcome_lines(oracle)],
                    [*_outcome_lines(actual)],
                    fromfile="full-parser",
                    tofile="mat-vis",
                    n=2,
                )
            )
            raise AssertionError(f"{args}\n{diff[:4000]}")
        code, stdout, stderr = actual
        combined = stdout + stderr
        if "choose from" in combined:
            clause = combined.split("choose from", 1)[1]
            cursor = 0
            for name in _COMMANDS:
                found = clause.find(name, cursor)
                assert found >= 0, (args, name, clause)
                cursor = found + len(name)
        if code == 0:
            commands = [token for token in args if not token.startswith("-")]
            assert commands, (args, stdout)
            assert markers[commands[0]] in stdout
            assert len(stdout) > 400
        else:
            assert code == 2, (args, code, stderr)


def _outcome_lines(outcome: tuple[int, str, str]):
    code, stdout, stderr = outcome
    yield f"code {code}\n"
    yield from stdout.splitlines(keepends=True)
    yield from stderr.splitlines(keepends=True)
