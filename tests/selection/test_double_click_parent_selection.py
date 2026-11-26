"""Double-click promotion selects one molecule instance or one disorder PART.

The gesture commits a single ``molecule`` or ``part`` target. Overlay paint
includes every visible atom of that parent and every visible bond that
shares it. Group 0 is ordered membership, not a PART, and a missing
molecule is not expanded into a scene-wide group.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mat_viewer.render.picking import (
    atom_instance_id,
    atom_parent_targets,
    bond_parent_targets,
    promoted_selection_parent,
)
from mat_viewer.render.selection import _atom_selection_trace, _bond_selection_trace
from mat_viewer.scene import build_scene_from_cif, scene_style

ROOT = Path(__file__).resolve().parents[2]
WATER = ROOT / "benchmarks" / "structures" / "molecular_water.cif"
CATION = ROOT / "tests" / "fixtures" / "synthetic_disordered_cation.cif"


def _scene(path: Path):
    return build_scene_from_cif(
        name=path.stem,
        title=path.stem,
        cif_path=str(path),
        show_hydrogen=True,
        display_mode="formula_unit",
    )


def _fragment(scene, index):
    labels = scene.get("atom_fragment_labels") or []
    if index < len(labels):
        return labels[index]
    return None


def _atom_parents(scene, index):
    return atom_parent_targets(scene["draw_atoms"][index], _fragment(scene, index))


def _members(scene, parent_id):
    atoms = scene["draw_atoms"]
    labels = scene.get("atom_fragment_labels") or []
    atom_indexes = [
        index
        for index, atom in enumerate(atoms)
        if any(item["id"] == parent_id for item in atom_parent_targets(atom, _fragment(scene, index)))
    ]
    bonds = [
        bond
        for bond in scene.get("bonds") or []
        if any(item["id"] == parent_id for item in bond_parent_targets(bond, atoms, labels))
    ]
    return atom_indexes, bonds


def _labels(scene, indexes):
    atoms = scene["draw_atoms"]
    return {atoms[index]["label"] for index in indexes}


def _bond_labels(scene, bonds):
    atoms = scene["draw_atoms"]
    return {tuple(sorted((atoms[bond["i"]]["label"], atoms[bond["j"]]["label"]))) for bond in bonds}


def test_molecular_crystal_promotes_to_that_molecule_instance():
    scene = _scene(WATER)
    atoms = scene["draw_atoms"]
    parents = _atom_parents(scene, 0)
    assert [item["kind"] for item in parents] == ["molecule"]
    molecule = promoted_selection_parent(parents)
    assert molecule is not None and molecule["kind"] == "molecule"
    indexes, bonds = _members(scene, molecule["id"])
    assert _labels(scene, indexes) == {"O1", "H1", "H2"}
    assert _bond_labels(scene, bonds) == {("H1", "O1"), ("H2", "O1"), ("H1", "H2")}
    assert len(bonds) == len(scene["bonds"])

    image = {**atoms[0], "_image_shift": [1, 0, 0]}
    image_parent = atom_parent_targets(image)[0]
    assert image_parent["id"] != molecule["id"]
    shifted = {**atoms[1], "_image_shift": [1, 0, 0]}
    extended = [*atoms, image, shifted]
    assert any(
        item["id"] == molecule["id"]
        for item in bond_parent_targets({"i": 0, "j": 1}, extended)
    )
    assert bond_parent_targets({"i": 0, "j": 4}, extended) == []
    assert all(
        item["id"] != molecule["id"]
        for item in bond_parent_targets({"i": 3, "j": 4}, extended)
    )

    other_symmetry = {**atoms[0], "_symop_index": 3}
    assert atom_parent_targets(other_symmetry)[0]["id"] != molecule["id"]
    other_molecule = {
        **atoms[0],
        "label": "O2",
        "_source_index": 20,
        "_source_molecule_index": 4,
    }
    assert atom_parent_targets(other_molecule)[0]["id"] != molecule["id"]


@pytest.mark.filterwarnings("ignore:Structure contains disorder:UserWarning")
def test_disorder_part_promotes_inside_one_molecule_only():
    scene = _scene(CATION)
    atoms = scene["draw_atoms"]
    by_label = {atom["label"]: index for index, atom in enumerate(atoms)}
    carbon = _atom_parents(scene, by_label["C1"])
    part = promoted_selection_parent(carbon)
    molecule = next(item for item in carbon if item["kind"] == "molecule")
    assert part is not None and part["kind"] == "part"
    assert part["parent_id"] == molecule["id"]
    assert part["label"] == "1"

    part_indexes, part_bonds = _members(scene, part["id"])
    assert _labels(scene, part_indexes) == {"C1", "C2", "C3", "C4"}
    assert _bond_labels(scene, part_bonds) == {
        ("C1", "N1"),
        ("C2", "N1"),
        ("C3", "N1"),
        ("C4", "N1"),
    }

    complement = promoted_selection_parent(_atom_parents(scene, by_label["C5"]))
    assert complement is not None and complement["id"] != part["id"]
    complement_indexes, complement_bonds = _members(scene, complement["id"])
    assert _labels(scene, complement_indexes) == {"C5", "C6", "C7", "C8"}
    assert set(part_indexes).isdisjoint(complement_indexes)
    assert _bond_labels(scene, part_bonds).isdisjoint(_bond_labels(scene, complement_bonds))

    # The ordered nitrogen belongs to the molecule and to neither PART.
    nitrogen = _atom_parents(scene, by_label["N1"])
    assert [item["kind"] for item in nitrogen] == ["molecule"]
    assert promoted_selection_parent(nitrogen)["id"] == molecule["id"]
    molecule_indexes, molecule_bonds = _members(scene, molecule["id"])
    assert _labels(scene, molecule_indexes) == {
        "N1", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8",
    }
    assert len(molecule_bonds) == 8
    chlorine = _labels(scene, [
        index for index, atom in enumerate(atoms) if atom["elem"] == "Cl"
    ])
    assert chlorine.isdisjoint(_labels(scene, molecule_indexes))

    # The N–C1 bond promotes to PART 1. A bond between the two PARTs does not.
    nitrogen_carbon = next(
        bond for bond in scene["bonds"]
        if {atoms[bond["i"]]["label"], atoms[bond["j"]]["label"]} == {"N1", "C1"}
    )
    labels = scene.get("atom_fragment_labels") or []
    assert promoted_selection_parent(
        bond_parent_targets(nitrogen_carbon, atoms, labels)
    )["id"] == part["id"]
    crossed = bond_parent_targets(
        {"i": by_label["C1"], "j": by_label["C5"]},
        atoms,
        labels,
    )
    assert [item["kind"] for item in crossed] == ["molecule"]

    intruder = {
        "label": "C9",
        "elem": "C",
        "_source_index": 50,
        "_source_molecule_index": 99,
        "dg": "1",
        "da": "A",
    }
    intruder_part = promoted_selection_parent(atom_parent_targets(intruder))
    assert intruder_part is not None
    assert intruder_part["label"] == "1"
    assert intruder_part["id"] != part["id"]

    style = scene_style(scene, {"show_hydrogen": True})
    atom_trace = _atom_selection_trace(scene, style).to_plotly_json()
    for row, parents in zip(atom_trace["customdata"], atom_trace["meta"]["mv_parents"], strict=True):
        index = int(row[1])
        assert parents == _atom_parents(scene, index)
    bond_trace = _bond_selection_trace(scene, style).to_plotly_json()
    for row, parents in zip(bond_trace["customdata"], bond_trace["meta"]["mv_parents"], strict=True):
        bond = next(
            item for item in scene["bonds"]
            if {row[5], row[6]} == {
                atom_instance_id(atoms[item["i"]], item["i"]),
                atom_instance_id(atoms[item["j"]], item["j"]),
            }
        )
        assert parents == bond_parent_targets(bond, atoms, labels)


def test_missing_molecule_membership_has_no_parent():
    orphan = {"label": "C1", "elem": "C", "dg": "0", "da": "."}
    assert atom_parent_targets(orphan) == []
    assert bond_parent_targets(
        {"i": 0, "j": 1},
        [orphan, {**orphan, "label": "C2", "_source_index": 1}],
    ) == []
    assert promoted_selection_parent([]) is None
    assert promoted_selection_parent(atom_parent_targets(orphan)) is None

    ordered = {
        "label": "O1",
        "elem": "O",
        "_source_index": 0,
        "_source_molecule_index": 0,
        "dg": "0",
        "da": ".",
        "part": "0",
    }
    parents = atom_parent_targets(ordered)
    assert [item["kind"] for item in parents] == ["molecule"]
    assert "disorder:" not in parents[0]["id"]
    assert "part:" not in parents[0]["id"]
    assert promoted_selection_parent(parents)["kind"] == "molecule"
