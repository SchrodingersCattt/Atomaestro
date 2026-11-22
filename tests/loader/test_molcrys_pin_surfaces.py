"""The shared MolCrysKit pin keeps both parent surfaces.

``2a3ee11e4c708b64691181cc2cbc4b2a3625f0c6`` is the authorized pin. It
includes Mercury ``read_mryx`` commit ``0702ed8d``, lightweight CIF commit
``a4080132``, and the label bound that discards unselected structures. It
is not MolCrysKit main and not tip ``7b859dd``.
``read_mryx`` imports ``defusedxml`` from the desktop extra, so a core-only
install of this commit fails here.
"""

from __future__ import annotations


def test_shared_pin_has_mercury_reader_and_lightweight_cif():
    import defusedxml
    import molcrys_kit.compat.cifparse as cifparse
    import molcrys_kit.io as io
    from molcrys_kit.structures.records import BondRecord

    assert defusedxml.__name__ == "defusedxml"
    assert "read_mryx" in io.__all__
    assert "read_mryx_atoms" in io.__all__
    assert callable(io.read_mryx)
    assert callable(io.read_mryx_atoms)
    assert hasattr(cifparse, "CifParser")
    assert "role" in BondRecord.__annotations__
