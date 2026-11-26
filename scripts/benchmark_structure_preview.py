"""Matched-size synthetic benchmark for server-rendered structure previews.

The scene is an explicit C-H pair grid of several hundred atoms. Run from
the repository root:

    python scripts/benchmark_structure_preview.py --browser --inline-html

``--browser`` records headless Chrome paint entries and WebGL context
counts when Chrome is installed. Those observations are not hardware-GPU
timings. ``--inline-html`` also writes one self-contained Plotly document.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from mat_viewer.render.preview_benchmark import main

    main()
