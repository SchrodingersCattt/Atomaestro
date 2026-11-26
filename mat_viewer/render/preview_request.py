"""Preview request, cache identity, and the synthetic pair-grid fixture.

The cache and the host-page writers live in sibling modules so this file
stays a request contract.
"""

from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from .contracts import RenderPlan

PREVIEW_REQUEST_SCHEMA = "mattervis.preview-request/v1"
PREVIEW_IMAGE_SCHEMA = "mattervis.preview-image/v1"
PREVIEW_CACHE_SCHEMA = "mattervis.preview-cache/v1"
PREVIEW_HOST_SCHEMA = "mattervis.preview-host/v1"

PREVIEW_BACKENDS = ("cpu", "gpu")
PREVIEW_FORMATS = ("png", "webp")

# PNG bytes match ``cpu.raster.render_png`` (compress_level 9, optimize off).
PNG_COMPRESS_LEVEL = 9
PNG_OPTIMIZE = False
WEBP_QUALITY = 80
WEBP_METHOD = 4
WEBP_LOSSLESS = False

# A preview is the requested pixel size. ``mat-vis render`` still defaults
# RenderSpec.scale to 2 for publication supersampling.
DEFAULT_PREVIEW_WIDTH = 900
DEFAULT_PREVIEW_HEIGHT = 720
DEFAULT_PREVIEW_SCALE = 1

MAX_PREVIEW_EDGE_PX = 4096
MAX_PREVIEW_SCALE = 4
MAX_PREVIEW_PIXELS = 4096 * 2048
MAX_NAMESPACE_LENGTH = 128
MAX_STRUCTURE_TEXT_BYTES = 32 * 1024 * 1024

PREVIEW_CACHE_MAX_ENTRIES = 32
PREVIEW_CACHE_MAX_BYTES = 64 * 1024 * 1024
MAX_ACTIVE_VIEWERS = 1

TIMING_NOTE = (
    "timing_s values are time.perf_counter spans in this process. "
    "They are not GPU timestamps, first contentful paint, or time to interactive."
)
RASTER_NOTE = (
    "Raster preview is a static CPU or GPU image. It does not create a "
    "WebGL context and is not pixel-identical to the Plotly viewer."
)

_STYLE_OVERRIDE_FIELDS = ("width", "height", "scale", "backend")


class PreviewError(ValueError):
    """The preview request is invalid or cannot be replayed."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class PreviewUnsupported(PreviewError):
    """The requested backend or encoder is outside the preview contract."""


class PreviewNotFound(PreviewError):
    """The ticket is unknown in this process."""


@dataclass(frozen=True, slots=True)
class TextStructure:
    """CIF or XYZ text supplied by a host instead of a filesystem path."""

    fmt: str
    text: str

    def __post_init__(self) -> None:
        if self.fmt not in {"cif", "xyz"}:
            raise PreviewError("text structure format must be cif or xyz")
        if not isinstance(self.text, str) or not self.text.strip():
            raise PreviewError("text structure is empty")
        encoded = self.text.encode("utf-8")
        if len(encoded) > MAX_STRUCTURE_TEXT_BYTES:
            raise PreviewError(
                f"structure text exceeds {MAX_STRUCTURE_TEXT_BYTES} bytes"
            )


@dataclass(frozen=True, slots=True)
class PreviewImage:
    """One retained or just-rendered preview raster."""

    schema: str
    media_type: str
    image_format: str
    backend: str
    width: int
    height: int
    data: bytes
    sha256: str
    cache_key: str
    cache_status: str
    rasterized: bool
    retained: bool
    ticket: str | None
    plan_sha256: str
    camera: dict[str, Any]
    summary: dict[str, Any]
    warnings: tuple[str, ...]
    timing_s: dict[str, float]
    namespace: str
    timing_note: str = TIMING_NOTE

    def public_dict(self) -> dict[str, Any]:
        """JSON envelope. Image bytes stay on the ticket URL."""

        ticket = self.ticket
        image_path = f"/api/v2/preview/image/{ticket}" if ticket else None
        activation = f"/api/v2/preview/activate/{ticket}" if ticket else None
        return {
            "schema": self.schema,
            "media_type": self.media_type,
            "format": self.image_format,
            "backend": self.backend,
            "width": self.width,
            "height": self.height,
            "byte_length": len(self.data),
            "sha256": self.sha256,
            "cache_key": self.cache_key,
            "cache_status": self.cache_status,
            "rasterized": self.rasterized,
            "retained": self.retained,
            "ticket": ticket,
            "image_path": image_path,
            "plan_sha256": self.plan_sha256,
            "camera": self.camera,
            "summary": self.summary,
            "warnings": list(self.warnings),
            "timing_s": self.timing_s,
            "timing_note": self.timing_note,
            "namespace": self.namespace,
            "interactive": False,
            "activation": {
                "method": "GET",
                "path": activation,
                "max_active_viewers": MAX_ACTIVE_VIEWERS,
                "kind": "one-plotly-document",
                "note": (
                    "Activation returns one complete Plotly document for this "
                    "preview's RenderPlan. The raster itself is not a viewer."
                ),
            },
        }


def json_ready(value: Any) -> Any:
    """Convert a request value into canonical JSON data."""

    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PreviewError("preview values must be finite")
        return value
    if isinstance(value, Path):
        return str(value)
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy is a core dependency
        np = None
    if np is not None and isinstance(value, np.generic):
        return json_ready(value.item())
    if np is not None and isinstance(value, np.ndarray):
        return json_ready(value.tolist())
    if is_dataclass(value) and not isinstance(value, type):
        return json_ready(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    raise PreviewError(
        f"cannot cache a preview value of type {type(value).__name__}"
    )


def encoder_descriptor(image_format: str) -> dict[str, Any]:
    """Encoder settings that participate in the cache key."""

    if image_format == "png":
        return {
            "format": "png",
            "compress_level": PNG_COMPRESS_LEVEL,
            "optimize": PNG_OPTIMIZE,
        }
    if image_format == "webp":
        return {
            "format": "webp",
            "quality": WEBP_QUALITY,
            "method": WEBP_METHOD,
            "lossless": WEBP_LOSSLESS,
        }
    raise PreviewUnsupported(
        f"preview format must be png or webp; got {image_format!r}"
    )


def preview_cache_key(material: Mapping[str, Any]) -> str:
    """SHA-256 hex digest of the canonical cache-key JSON.

    ``material`` is the object documented in ``docs/agents/preview_api.md``.
    Key order does not matter. The namespace is not part of this object.
    """

    encoded = json.dumps(
        json_ready(material),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _package_version() -> str:
    from .. import __version__

    return str(__version__)


def _positive_int(value: Any, name: str, *, limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PreviewError(f"{name} must be a positive integer")
    if value <= 0 or value > limit:
        raise PreviewError(f"{name} must be in 1..{limit}")
    return value


def _style_mapping(style: Any) -> dict[str, Any]:
    if style is None:
        return {}
    if is_dataclass(style) and not isinstance(style, type):
        return dict(asdict(style))
    if isinstance(style, Mapping):
        return dict(style)
    raise PreviewError("style must be a RenderSpec or a mapping")


def _resolve_backend(backend: str) -> str:
    name = str(backend).strip().lower()
    if name == "matplotlib":
        raise PreviewUnsupported(
            "Matplotlib is the projected 2D PNG/PDF/SVG exporter and is not "
            "the interactive structure preview. Preview rasters use backend "
            "'cpu' or 'gpu'.",
            hint="Use mat-vis render --backend matplotlib for a 2D export.",
        )
    if name == "plotly":
        raise PreviewUnsupported(
            "The Plotly backend writes the interactive viewer. Request a "
            "cpu or gpu preview, then activate that ticket for one Plotly "
            "document.",
            hint="POST /api/v2/preview with backend cpu or gpu.",
        )
    if name not in PREVIEW_BACKENDS:
        raise PreviewUnsupported(
            "preview backend must be 'cpu' or 'gpu'; "
            f"got {backend!r}"
        )
    return name


def _check_namespace(namespace: str) -> str:
    if not isinstance(namespace, str) or not namespace.strip():
        raise PreviewError("namespace must be a non-empty string")
    if len(namespace) > MAX_NAMESPACE_LENGTH:
        raise PreviewError(
            f"namespace must be at most {MAX_NAMESPACE_LENGTH} characters"
        )
    return namespace


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    """Resolved preview request. Dimensions override those style fields."""

    namespace: str
    source: Any
    camera: Any = None
    view: Any = None
    style: Any = None
    width: int = DEFAULT_PREVIEW_WIDTH
    height: int = DEFAULT_PREVIEW_HEIGHT
    scale: int = DEFAULT_PREVIEW_SCALE
    backend: str = "cpu"
    image_format: str = "png"
    atom_groups: Any = None
    bond_groups: Any = None
    cell_overlays: Any = None
    vector_overlays: Any = None
    topology_data: Any = None
    atom_property_color: Any = None
    _temp_path: Path | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "namespace", _check_namespace(self.namespace))
        backend = _resolve_backend(self.backend)
        image_format = str(self.image_format).strip().lower()
        if image_format not in PREVIEW_FORMATS:
            raise PreviewUnsupported(
                f"preview format must be png or webp; got {self.image_format!r}"
            )
        if backend == "gpu" and image_format != "png":
            raise PreviewUnsupported(
                "GPU previews are PNG only. WebP encodes the CPU raster."
            )
        width = _positive_int(self.width, "width", limit=MAX_PREVIEW_EDGE_PX)
        height = _positive_int(self.height, "height", limit=MAX_PREVIEW_EDGE_PX)
        scale = _positive_int(self.scale, "scale", limit=MAX_PREVIEW_SCALE)
        pixels = width * height * scale * scale
        if pixels > MAX_PREVIEW_PIXELS:
            raise PreviewError(
                "preview pixel count exceeds "
                f"{MAX_PREVIEW_PIXELS} (width * height * scale^2)"
            )
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "image_format", image_format)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "scale", scale)

    def render_spec_payload(self) -> dict[str, Any]:
        payload = _style_mapping(self.style)
        payload["width"] = self.width
        payload["height"] = self.height
        payload["scale"] = self.scale
        payload["backend"] = self.backend
        return payload

    def cache_material(self) -> dict[str, Any] | None:
        """Cache-key object, or None when the source is a live object.

        Live objects are keyed by the prepared plan fingerprint instead,
        because they have no canonical JSON form.
        """

        source_material = _source_material(self.source)
        if source_material is None:
            return None
        style = _style_mapping(self.style)
        for name in _STYLE_OVERRIDE_FIELDS:
            style.pop(name, None)
        return {
            "schema": PREVIEW_CACHE_SCHEMA,
            "package_version": _package_version(),
            "backend": self.backend,
            "format": self.image_format,
            "encoder": encoder_descriptor(self.image_format),
            "width": self.width,
            "height": self.height,
            "scale": self.scale,
            "view": None if self.view is None else json_ready(self.view),
            "camera": None if self.camera is None else json_ready(self.camera),
            "style": json_ready(style) or None,
            "source": source_material,
            "atom_groups": json_ready(self.atom_groups),
            "bond_groups": json_ready(self.bond_groups),
            "cell_overlays": json_ready(self.cell_overlays),
            "vector_overlays": json_ready(self.vector_overlays),
            "topology_data": json_ready(self.topology_data),
            "atom_property_color": json_ready(self.atom_property_color),
        }

    def prepare(self) -> RenderPlan:
        from ..agent import prepare_render

        source, temp_path = _loadable_source(self.source)
        object.__setattr__(self, "_temp_path", temp_path)
        try:
            plan = prepare_render(
                source,
                view=self.view,
                camera=self.camera,
                render_spec=self.render_spec_payload(),
                topology_data=self.topology_data,
                cell_overlays=self.cell_overlays,
                vector_overlays=self.vector_overlays,
                atom_groups=self.atom_groups,
                bond_groups=self.bond_groups,
                atom_property_color=self.atom_property_color,
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
                object.__setattr__(self, "_temp_path", None)
        if isinstance(self.source, TextStructure):
            # The temp path would change the plan fingerprint on every replay.
            metadata = dict(plan.metadata)
            metadata["source"] = None
            plan = replace(plan, metadata=metadata)
        return plan

    def request_record(self) -> dict[str, Any]:
        """JSON fields needed to prepare this request again."""

        style = _style_mapping(self.style)
        for name in _STYLE_OVERRIDE_FIELDS:
            style.pop(name, None)
        return {
            "camera": None if self.camera is None else json_ready(self.camera),
            "view": None if self.view is None else json_ready(self.view),
            "style": json_ready(style) or None,
            "width": self.width,
            "height": self.height,
            "scale": self.scale,
            "atom_groups": json_ready(self.atom_groups),
            "bond_groups": json_ready(self.bond_groups),
            "cell_overlays": json_ready(self.cell_overlays),
            "vector_overlays": json_ready(self.vector_overlays),
            "topology_data": json_ready(self.topology_data),
            "atom_property_color": json_ready(self.atom_property_color),
        }


def _source_material(source: Any) -> dict[str, Any] | None:
    if isinstance(source, TextStructure):
        return {"kind": source.fmt, "text": source.text}
    if isinstance(source, Mapping):
        return {"kind": "scene", "scene": json_ready(source)}
    if isinstance(source, (str, Path)):
        path = Path(source).expanduser()
        if not path.is_file():
            raise PreviewError(f"structure path does not exist: {path}")
        stat = path.stat()
        return {
            "kind": "path",
            "path": str(path.resolve()),
            "mtime_ns": int(stat.st_mtime_ns),
            "size": int(stat.st_size),
        }
    return None


def _loadable_source(source: Any) -> tuple[Any, Path | None]:
    if isinstance(source, TextStructure):
        with tempfile.NamedTemporaryFile(
            suffix=f".{source.fmt}",
            delete=False,
        ) as handle:
            path = Path(handle.name)
        path.write_text(source.text, encoding="utf-8")
        return path, path
    return source, None


def plan_cache_material(
    plan: RenderPlan,
    *,
    backend: str,
    image_format: str,
) -> dict[str, Any]:
    """Cache-key object for a live source after the plan exists."""

    return {
        "schema": PREVIEW_CACHE_SCHEMA,
        "package_version": _package_version(),
        "backend": backend,
        "format": image_format,
        "encoder": encoder_descriptor(image_format),
        "plan_sha256": plan.fingerprint(),
        "source": {"kind": "live"},
    }


def scene_summary(plan: RenderPlan) -> dict[str, Any]:
    """Count displayed atoms, bonds, and the periodic cell primitive."""

    atoms = 0
    bonds: set[str] = set()
    periodic_cell = False
    for primitive in plan.primitives:
        kind = primitive.metadata.get("kind")
        if kind == "atom":
            atoms += 1
        elif kind == "bond":
            parts = primitive.semantic_id.split(":")
            bonds.add(parts[1] if len(parts) >= 2 else primitive.semantic_id)
        if primitive.semantic_id == "unit-cell" or primitive.semantic_id.startswith(
            "unit-cell"
        ):
            periodic_cell = True
    return {
        "atoms": atoms,
        "bonds": len(bonds),
        "periodic_cell": periodic_cell,
        "primitives": len(plan.primitives),
    }


def camera_payload(plan: RenderPlan) -> dict[str, Any]:
    """Resolved world-space camera stored on the plan."""

    return json_ready(asdict(plan.camera))


def build_preview_request(
    source: Any,
    *,
    namespace: str,
    camera: Any = None,
    view: Any = None,
    style: Any = None,
    width: int | None = None,
    height: int | None = None,
    scale: int | None = None,
    backend: str = "cpu",
    image_format: str = "png",
    atom_groups: Any = None,
    bond_groups: Any = None,
    cell_overlays: Any = None,
    vector_overlays: Any = None,
    topology_data: Any = None,
    atom_property_color: Any = None,
) -> PreviewRequest:
    """Apply preview defaults. Explicit width, height, and scale win."""

    style_map = _style_mapping(style)
    resolved_width = DEFAULT_PREVIEW_WIDTH if width is None else width
    resolved_height = DEFAULT_PREVIEW_HEIGHT if height is None else height
    resolved_scale = DEFAULT_PREVIEW_SCALE if scale is None else scale
    if width is None and "width" in style_map:
        resolved_width = style_map["width"]
    if height is None and "height" in style_map:
        resolved_height = style_map["height"]
    if scale is None and "scale" in style_map:
        resolved_scale = style_map["scale"]
    return PreviewRequest(
        namespace=namespace,
        source=source,
        camera=camera,
        view=view,
        style=style,
        width=resolved_width,
        height=resolved_height,
        scale=resolved_scale,
        backend=backend,
        image_format=image_format,
        atom_groups=atom_groups,
        bond_groups=bond_groups,
        cell_overlays=cell_overlays,
        vector_overlays=vector_overlays,
        topology_data=topology_data,
        atom_property_color=atom_property_color,
    )

def synthetic_ch_pair_grid(
    *,
    nx: int = 1,
    ny: int = 1,
    nz: int = 1,
    spacing: float = 3.0,
    hydrogen_offset: float = 1.09,
    carbon_radius: float = 0.35,
    hydrogen_radius: float = 0.20,
) -> dict[str, Any]:
    """Artificial C-H pairs on a cubic grid.

    Coordinates, radii, and bonds are explicit. This is a preview fixture.
    It does not perceive bonds or represent a researched composition.
    """

    if min(nx, ny, nz) <= 0:
        raise PreviewError("synthetic grid counts must be positive")
    atoms: list[dict[str, Any]] = []
    bonds: list[dict[str, int]] = []
    index = 0
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                carbon = (
                    spacing * i + 0.75,
                    spacing * j + 0.75,
                    spacing * k + 0.75,
                )
                hydrogen = (
                    carbon[0] + hydrogen_offset,
                    carbon[1],
                    carbon[2],
                )
                atoms.append(
                    {
                        "elem": "C",
                        "cart": [carbon[0], carbon[1], carbon[2]],
                        "atom_radius": carbon_radius,
                    }
                )
                atoms.append(
                    {
                        "elem": "H",
                        "cart": [hydrogen[0], hydrogen[1], hydrogen[2]],
                        "atom_radius": hydrogen_radius,
                    }
                )
                bonds.append({"i": index, "j": index + 1})
                index += 2
    return {
        "atoms": atoms,
        "bonds": bonds,
        "matrix": [
            [nx * spacing, 0.0, 0.0],
            [0.0, ny * spacing, 0.0],
            [0.0, 0.0, nz * spacing],
        ],
    }


def preview_request_from_payload(payload: Mapping[str, Any]) -> PreviewRequest:
    """Parse a JSON preview body. Exactly one of scene, cif, or xyz."""

    if not isinstance(payload, Mapping):
        raise PreviewError("preview body must be a JSON object")
    allowed = {
        "namespace",
        "format",
        "backend",
        "width",
        "height",
        "scale",
        "view",
        "camera",
        "style",
        "scene",
        "cif",
        "xyz",
        "atom_groups",
        "bond_groups",
        "cell_overlays",
        "vector_overlays",
        "topology_data",
        "atom_property_color",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise PreviewError(f"unknown preview fields: {', '.join(unknown)}")
    present = [
        name
        for name in ("scene", "cif", "xyz")
        if payload.get(name) not in (None, "")
    ]
    if len(present) != 1:
        raise PreviewError("supply exactly one of scene, cif, or xyz")
    if "scene" in present:
        scene = payload["scene"]
        if not isinstance(scene, Mapping):
            raise PreviewError("scene must be a JSON object")
        source: Any = json_ready(scene)
    elif "cif" in present:
        if not isinstance(payload["cif"], str):
            raise PreviewError("cif must be a string")
        source = TextStructure(fmt="cif", text=payload["cif"])
    else:
        if not isinstance(payload["xyz"], str):
            raise PreviewError("xyz must be a string")
        source = TextStructure(fmt="xyz", text=payload["xyz"])
    return build_preview_request(
        source,
        namespace=str(payload.get("namespace") or ""),
        camera=payload.get("camera"),
        view=payload.get("view"),
        style=payload.get("style"),
        width=payload.get("width"),
        height=payload.get("height"),
        scale=payload.get("scale"),
        backend=str(payload.get("backend") or "cpu"),
        image_format=str(payload.get("format") or "png"),
        atom_groups=payload.get("atom_groups"),
        bond_groups=payload.get("bond_groups"),
        cell_overlays=payload.get("cell_overlays"),
        vector_overlays=payload.get("vector_overlays"),
        topology_data=payload.get("topology_data"),
        atom_property_color=payload.get("atom_property_color"),
    )
