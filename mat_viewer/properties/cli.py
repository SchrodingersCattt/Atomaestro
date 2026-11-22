"""Command-line options for continuous per-atom property colouring."""

from __future__ import annotations

import argparse

from . import AtomPropertyColorSpec, build_color_lut


def add_atom_property_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the per-atom colouring flags. The flag definitions live in ``cli_lazy``."""
    from ..cli_lazy import add_atom_property_arguments as _add

    _add(parser)


def atom_property_spec(args: argparse.Namespace) -> AtomPropertyColorSpec | None:
    if not getattr(args, "color_by", None):
        return None
    component = getattr(args, "color_component", None)
    if component is not None:
        try:
            component = int(component)
        except ValueError:
            pass
    spec = AtomPropertyColorSpec(
        fields=tuple(args.color_by),
        reduction=args.color_reduction,
        component=component,
        colormap=args.colormap,
        value_range=None if args.color_range is None else tuple(args.color_range),
        center=args.color_center,
        nan_color=args.nan_color,
        show_colorbar=bool(args.show_colorbar),
        label=args.color_label,
        unit=args.color_unit,
    )
    build_color_lut(spec.colormap)
    return spec
