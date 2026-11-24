"""Existing hydrogen atoms are drawn unless a caller hides them.

The fixtures are synthetic. They do not add hydrogen sites that the input
omitted, and they do not use catalog or research structures.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from mat_viewer.app import ViewerBackend
from mat_viewer.cli_lazy import add_session_arguments
from mat_viewer.loader import build_bundle_scene, build_loaded_crystal
from mat_viewer.presets import default_preset
from mat_viewer.render.cli import _build_render_parser
from mat_viewer.renderer import build_figure
from mat_viewer.tui.controller import TerminalViewController
from mat_viewer.tui.crystal_ir import AtomIR, CrystalIR, Lattice
from mat_viewer.tui.state import TerminalDisplayState


_WATER = """data_probe
_cell_length_a 10
_cell_length_b 10
_cell_length_c 10
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x,y,z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
O1 O 0.5000 0.5000 0.5000 1
H1 H 0.5957 0.5000 0.5000 1
H2 H 0.4760 0.5927 0.5000 1
"""

_SALT = """data_salt
_cell_length_a 5.64
_cell_length_b 5.64
_cell_length_c 5.64
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x,y,z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
Na1 Na 0.0 0.0 0.0 1
Cl1 Cl 0.5 0.0 0.0 1
"""


def _write(tmp: Path, name: str, text: str) -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return path


def _elements(scene) -> list[str]:
    return sorted(atom["elem"] for atom in scene["draw_atoms"])


def _atom_vertices(scene) -> int:
    figure = build_figure(scene, scene["style"])
    count = 0
    for trace in figure.data:
        meta = trace.meta if isinstance(trace.meta, dict) else {}
        if meta.get("mv_role") != "atom" or trace.visible in (False, "legendonly"):
            continue
        count += len(np.asarray(trace.x))
    return count


def _backend(tmp: Path, bundle):
    preset_path = tmp / "preset.json"
    if not preset_path.exists():
        preset_path.write_text(json.dumps(default_preset()), encoding="utf-8")
    backend = ViewerBackend(preset_path=str(preset_path), root_dir=str(tmp))
    backend._render_worker.request_figure_build = lambda *args, **kwargs: False
    backend.bundles[bundle.name] = bundle
    if bundle.name not in backend.structure_names:
        backend.structure_names.append(bundle.name)
    return backend


def test_loaded_scene_draws_existing_hydrogens_and_keeps_source_atoms(tmp_path):
    path = _write(tmp_path, "probe.cif", _WATER)
    bundle = build_loaded_crystal(
        name="probe",
        title="Probe",
        cif_path=str(path),
        preset=default_preset(),
        source="upload",
    )

    assert sorted(atom["elem"] for atom in bundle.raw_atoms) == ["H", "H", "O"]
    assert _elements(bundle.scene) == ["H", "H", "O"]
    assert bundle.scene["show_hydrogen"] is True
    assert bundle.scene["style"]["show_hydrogen"] is True
    omitted = build_bundle_scene(bundle, display_mode="formula_unit")
    hidden = build_bundle_scene(
        bundle, display_mode="formula_unit", show_hydrogen=False
    )
    assert _elements(omitted) == ["H", "H", "O"]
    assert _elements(hidden) == ["O"]
    assert len(hidden["_canonical_source_atoms"]) == 3
    assert _atom_vertices(omitted) > _atom_vertices(hidden)


def test_hydrogen_free_input_is_unchanged_by_the_display_default(tmp_path):
    path = _write(tmp_path, "salt.cif", _SALT)
    bundle = build_loaded_crystal(
        name="salt",
        title="Salt",
        cif_path=str(path),
        preset=default_preset(),
        source="upload",
    )
    shown = build_bundle_scene(bundle, display_mode="formula_unit")
    forced = build_bundle_scene(bundle, display_mode="formula_unit", show_hydrogen=True)
    assert _elements(bundle.scene) == ["Cl", "Na"]
    assert _elements(shown) == _elements(forced) == ["Cl", "Na"]
    assert all(atom["elem"] != "H" for atom in bundle.raw_atoms)
    assert _atom_vertices(shown) == _atom_vertices(forced)


def test_fresh_scenes_check_hydrogens_and_an_explicit_hide_removes_them(tmp_path):
    path = _write(tmp_path, "probe.cif", _WATER)
    bundle = build_loaded_crystal(
        name="probe",
        title="Probe",
        cif_path=str(path),
        preset=default_preset(),
        source="upload",
    )
    backend = _backend(tmp_path, bundle)
    try:
        backend.create_scene(structure=bundle.name)
        first = backend.current_state
        first_scene = backend.scene_for_state(first)
        backend.create_scene(structure=bundle.name)
        second = backend.current_state
        second_scene = backend.scene_for_state(second)
        assert "hydrogens" in first["display_options"]
        assert "hydrogens" in second["display_options"]
        assert first["scene_id"] != second["scene_id"]
        assert _elements(first_scene) == _elements(second_scene) == ["H", "H", "O"]
        hidden_options = [
            option for option in first["display_options"] if option != "hydrogens"
        ]
        hidden_state = backend.patch_state(
            {"display_options": hidden_options},
            scene_id=first["scene_id"],
        )
        hidden_scene = backend.scene_for_state(hidden_state)
        assert "hydrogens" not in hidden_state["display_options"]
        assert _elements(hidden_scene) == ["O"]
        assert _elements(backend.scene_for_state(second)) == ["H", "H", "O"]
    finally:
        backend.close()


def test_entry_flag_and_style_flag_share_one_fresh_scene_precedence(tmp_path):
    path = _write(tmp_path, "probe.cif", _WATER)
    cases = {
        "entry-only": ({"show_hydrogen": False}, ["O"], False),
        "style-only": ({"style": {"show_hydrogen": False}}, ["O"], False),
        "entry-hides-over-style": (
            {"show_hydrogen": False, "style": {"show_hydrogen": True}},
            ["O"],
            False,
        ),
        "entry-shows-over-style": (
            {"show_hydrogen": True, "style": {"show_hydrogen": False}},
            ["H", "H", "O"],
            True,
        ),
    }
    for name, (entry, drawn, checked) in cases.items():
        preset = default_preset()
        preset["structures"]["probe"] = entry
        bundle = build_loaded_crystal(
            name="probe",
            title="Probe",
            cif_path=str(path),
            preset=preset,
            source="upload",
        )
        assert sorted(atom["elem"] for atom in bundle.raw_atoms) == ["H", "H", "O"]
        assert _elements(bundle.scene) == drawn, name
        preset_path = tmp_path / f"{name}.json"
        preset_path.write_text(json.dumps(preset), encoding="utf-8")
        backend = ViewerBackend(preset_path=str(preset_path), root_dir=str(tmp_path))
        backend._render_worker.request_figure_build = lambda *args, **kwargs: False
        backend.bundles[bundle.name] = bundle
        if bundle.name not in backend.structure_names:
            backend.structure_names.append(bundle.name)
        try:
            state = backend.default_state(bundle.name)
            assert ("hydrogens" in state["display_options"]) is checked, name
            scene = backend.scene_for_state(state)
            assert _elements(scene) == drawn, name
            assert len(scene["_canonical_source_atoms"]) == 3, name
        finally:
            backend.close()


def test_saved_hide_restores_and_a_session_hide_stays_hidden(tmp_path):
    path = _write(tmp_path, "probe.cif", _WATER)
    bundle = build_loaded_crystal(
        name="probe",
        title="Probe",
        cif_path=str(path),
        preset=default_preset(),
        source="upload",
    )
    backend = _backend(tmp_path, bundle)
    try:
        state = backend.default_state(bundle.name)
        assert "hydrogens" in state["display_options"]
        backend.create_scene(structure=bundle.name)
        scene_id = backend.current_state["scene_id"]
        hidden_options = [
            option
            for option in backend.current_state["display_options"]
            if option != "hydrogens"
        ]
        hidden_state = backend.patch_state(
            {"display_options": hidden_options},
            scene_id=scene_id,
        )
        assert "hydrogens" not in hidden_state["display_options"]
        assert _elements(backend.scene_for_state(hidden_state)) == ["O"]
        backend.save_preset()
    finally:
        backend.close()
    restored = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )
    restored._render_worker.request_figure_build = lambda *args, **kwargs: False
    restored.bundles[bundle.name] = bundle
    if bundle.name not in restored.structure_names:
        restored.structure_names.append(bundle.name)
    try:
        fresh = restored.default_state(bundle.name)
        assert "hydrogens" not in fresh["display_options"]
        assert _elements(restored.scene_for_state(fresh)) == ["O"]
        assert len(restored.scene_for_state(fresh)["_canonical_source_atoms"]) == 3
    finally:
        restored.close()


def test_style_override_hides_hydrogens_without_removing_source_atoms(tmp_path):
    path = _write(tmp_path, "probe.cif", _WATER)
    preset = default_preset()
    preset["style"]["show_hydrogen"] = False
    preset["structures"]["probe"] = {"style": {"show_hydrogen": False}}
    bundle = build_loaded_crystal(
        name="probe",
        title="Probe",
        cif_path=str(path),
        preset=preset,
        source="upload",
    )
    assert sorted(atom["elem"] for atom in bundle.raw_atoms) == ["H", "H", "O"]
    assert _elements(bundle.scene) == ["O"]
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps(preset), encoding="utf-8")
    backend = _backend(tmp_path, bundle)
    try:
        state = backend.default_state(bundle.name)
        assert "hydrogens" not in state["display_options"]
        backend.create_scene(structure=bundle.name)
        scene = backend.scene_for_state(backend.current_state)
        assert _elements(scene) == ["O"]
    finally:
        backend.close()


def test_render_and_terminal_defaults_show_hydrogens_until_hidden():
    parser = argparse.ArgumentParser()
    _build_render_parser(parser.add_subparsers(dest="command"))
    render = parser.parse_args(["render", "probe.cif", "-o", "probe.png"])
    hidden = parser.parse_args(
        ["render", "probe.cif", "-o", "probe.png", "--no-hydrogen"]
    )
    assert render.show_hydrogen is True
    assert hidden.show_hydrogen is False

    session = argparse.ArgumentParser()
    add_session_arguments(session)
    assert session.parse_args([]).hydrogen is True
    assert session.parse_args(["--no-hydrogen"]).hydrogen is False

    display = TerminalDisplayState(
        display_level="atom",
        label_mode="element",
        show_bonds=True,
        show_cell=True,
        show_minor=False,
        mono=False,
    )
    assert display.show_hydrogen is True
    crystal = CrystalIR(
        title="probe",
        formula="H",
        lattice=Lattice(1, 1, 1, 90, 90, 90, np.eye(3)),
        atoms=[
            AtomIR(
                "H",
                np.zeros(3),
                np.zeros(3),
                atom_id="site:H1",
                label="H1",
                index=0,
                display_copy_id="copy:H1",
            )
        ],
    )
    controller = TerminalViewController(crystal, width=20, height=8)
    assert controller.state.display.show_hydrogen is True
