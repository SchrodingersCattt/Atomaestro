"""Deferred bindings and argparse fragments that must stay import-light.

Importing this module must not import numpy, ASE, or ``mat_viewer.render``.
Symbols that need the scientific stack resolve on first call so ``mat-vis``
help and the lightweight commands can load ``mat_viewer.cli`` without them.
The argument helpers live here because their original modules sit under
package inits that import that stack. The bindings live outside ``cli.py``
so that module can stay within its line cap.
"""

from __future__ import annotations

import argparse
import inspect
from functools import update_wrapper
from importlib import import_module
from pathlib import Path
from typing import Any

_BINDINGS: dict[str, tuple[str, str]] = {
    "_add_render_control_arguments": (".render.cli_controls", "_add_render_control_arguments"),
    "_animation_time_from_args": (".render.cli_controls", "_animation_time_from_args"),
    "_atom_property_spec": (".properties.cli", "atom_property_spec"),
    "_camera_request": (".render.cli_controls", "_camera_request"),
    "_file_sha256": (".structure.inspect", "file_sha256"),
    "_frame_annotation_from_args": (".render.cli_controls", "_frame_annotation_from_args"),
    "_inspect_payload": (".structure.inspect", "inspect_payload"),
    "_is_animation_output": (".render.cli_controls", "_is_animation_output"),
    "_is_nonperiodic_structure": (".structure.inspect", "is_nonperiodic_structure"),
    "_load_overlay_file": (".render.overlay.io", "load_overlay_file"),
    "_render_ortep_mode": (".render.cli_controls", "_render_ortep_mode"),
    "_render_shading": (".render.cli_controls", "_render_shading"),
    "_style_groups": (".render.cli_controls", "_style_groups"),
    "_validate_render_options": (".render.cli_controls", "_validate_render_options"),
    "add_batch_render_arguments": (".render.fast_cli", "add_batch_render_arguments"),
    "bond_thresholds_from_args": (".render.cli_controls", "bond_thresholds_from_args"),
    "render_batch_if_selected": (".render.fast_cli", "render_batch_if_selected"),
}


_TARGET_METADATA = frozenset({
    "__annotations__",
    "__doc__",
    "__name__",
    "__qualname__",
})


class _Deferred:
    """Call a module attribute, importing that module on first use.

    Reading ``__name__``, ``__doc__`` or the signature resolves the target and
    then reports that function. Importing this module does not.
    """

    def __init__(self, module: str, name: str) -> None:
        self._module = module
        self._name = name
        self._fn: Any = None

    def _resolve(self) -> Any:
        function = self._fn
        if function is None:
            function = getattr(import_module(self._module, __package__), self._name)
            self._fn = function
            update_wrapper(self, function)
        return function

    def __getattribute__(self, name: str) -> Any:
        if name in _TARGET_METADATA or name == "__signature__":
            function = object.__getattribute__(self, "_resolve")()
            if name == "__signature__":
                return inspect.signature(function)
            return getattr(function, name)
        return object.__getattribute__(self, name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


def add_atom_property_arguments(parser: argparse.ArgumentParser) -> None:
    """Add per-atom colouring flags. This function does not import NumPy."""
    parser.add_argument(
        "--property-data",
        type=Path,
        default=None,
        metavar="MANIFEST",
        help="JSON + mmap NPY atom-property sidecar manifest.",
    )
    parser.add_argument(
        "--color-by",
        nargs="+",
        default=None,
        metavar="FIELD",
        help="Per-atom field(s), qualified as array:, column:, or sidecar:.",
    )
    parser.add_argument(
        "--color-reduction",
        choices=(
            "auto",
            "scalar",
            "magnitude",
            "component",
            "trace",
            "mean_normal",
            "von_mises",
        ),
        default="auto",
    )
    parser.add_argument("--color-component", default=None, metavar="NAME_OR_INDEX")
    parser.add_argument("--colormap", default="viridis", metavar="NAME")
    parser.add_argument(
        "--color-range", nargs=2, type=float, default=None, metavar=("MIN", "MAX")
    )
    parser.add_argument("--color-center", type=float, default=None, metavar="VALUE")
    parser.add_argument("--nan-color", default="#BDBDBD", metavar="COLOR")
    parser.add_argument("--color-label", default=None, metavar="TEXT")
    parser.add_argument("--color-unit", default=None, metavar="UNIT")
    parser.add_argument(
        "--no-colorbar", action="store_false", dest="show_colorbar", default=True
    )


def add_bond_policy_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the shared MolCrysKit connectivity controls to a parser."""
    parser.add_argument(
        "--bond-scale",
        type=float,
        default=None,
        metavar="SCALE",
        help="Positive MolCrysKit bond-perception scale (default: 1.0).",
    )
    parser.add_argument(
        "--bond-threshold",
        action="append",
        default=[],
        dest="bond_threshold_specs",
        metavar="ELEMENT1,ELEMENT2=CUTOFF",
        help=(
            "Override one MolCrysKit element-pair cutoff in Å; repeat the "
            "option for multiple pairs."
        ),
    )


def add_session_arguments(parser: argparse.ArgumentParser) -> None:
    """Add machine-readable terminal rendering and session options."""
    parser.add_argument(
        "--hydrogen",
        action="store_true",
        default=False,
        help="Draw hydrogen atoms. Hidden by default so heavy atoms stay readable.",
    )
    parser.add_argument(
        "--boundary",
        action="store_true",
        default=False,
        help="Draw periodic boundary replicas. Hidden by default.",
    )
    parser.add_argument(
        "--charset",
        choices=("unicode", "ascii7"),
        default="unicode",
        help="Static/session glyph set (default: unicode; ascii7 implies monochrome).",
    )
    parser.add_argument(
        "--session-format",
        choices=("jsonl",),
        default=None,
        help="Keep one stateful session on stdin/stdout using newline-delimited JSON.",
    )
    parser.add_argument(
        "--repeat", type=int, nargs=3, metavar=("NA", "NB", "NC"), default=None,
        help="Show an NA x NB x NC supercell instead of one unit cell.",
    )
    parser.add_argument(
        "--grow-radius", type=float, default=None, metavar="ANGSTROM",
        help="Add periodic images of atoms within this distance of the cell contents.",
    )
    parser.add_argument("--parallax", action="store_true", help="Experimental previous-rotation ghost (off by default).")
    parser.add_argument("--no-depth", action="store_true", help="Hide textual depth-band suffixes.")
    parser.add_argument("--slice-axis", choices=("off", "depth", "a", "b", "c"), default="off")
    parser.add_argument("--slice-index", type=int, default=0)
    parser.add_argument("--slice-width", type=float, default=1.0, help="Layer width in Angstrom (depth) or fractional coordinates (a/b/c).")
    parser.add_argument(
        "--axes", action=argparse.BooleanOptionalAction, default=True,
        help="Fit the scene above a bottom row with an a/b/c triad and a scale bar.",
    )
    parser.add_argument(
        "--renderer",
        default=None,
        help=(
            "Terminal frame renderer id. Default mattervis.native-v1 keeps "
            "today's picture. A missing or incompatible id fails closed."
        ),
    )
    parser.add_argument(
        "--renderer-config",
        default=None,
        metavar="FILE",
        help=(
            "JSON renderer config (mattervis.tui.renderer_config/v1): options, "
            "bond policy, and optional package/digest pins."
        ),
    )
    parser.add_argument(
        "--observation-profile",
        choices=("legacy", "model"),
        default="legacy",
        help=(
            "JSONL observation profile. legacy is unchanged. model drops "
            "chemistry warnings, formulas, and the source title."
        ),
    )


def bind_deferred_cli_symbols(namespace: dict[str, Any]) -> None:
    """Install lazy command helpers into ``namespace`` (``cli`` globals)."""
    for key, (module, name) in _BINDINGS.items():
        namespace[key] = _Deferred(module, name)


def fail_cli(message: str, *, json_output: bool, emit, code: int = 2, details: dict | None = None) -> None:
    """Print a CLI error and exit. ``details`` is included in ``--json`` output."""

    import sys

    print(f"Error: {message}", file=sys.stderr)
    if json_output:
        payload = {"schema": "mattervis.error/v1", "ok": False, "error": message}
        if details:
            payload["details"] = details
        emit(payload, json_output=True)
    raise SystemExit(code)


def fail_cli_exception(exc: BaseException, *, json_output: bool, fail) -> None:
    """Exit from a render failure, keeping polyhedron shell errors structured."""

    from .topology.disorder_policy import PolyhedronShellError

    details = exc.to_dict() if isinstance(exc, PolyhedronShellError) else None
    fail(str(exc), json_output=json_output, details=details)
