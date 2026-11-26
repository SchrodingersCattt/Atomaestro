"""Cached CPU/GPU image previews and one-viewer activation.

A host submits a structure, style, camera, and pixel size. The service
returns a PNG or WebP rendered from the same ``RenderPlan`` the Plotly
adapter uses. The image is a static raster: it does not create a WebGL
context. Activating a retained preview returns one complete Plotly
document for that plan.

The in-process cache is partitioned by namespace. The cache key identifies
the raster and does not include the namespace. Tickets are bearer
capabilities for one namespace's retained entry. Restarting the process
drops every ticket. Entries are not shared across namespaces.
"""

from __future__ import annotations

import json
import math
import secrets
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
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


def encode_preview_raster(
    plan: RenderPlan,
    *,
    backend: str,
    image_format: str,
) -> bytes:
    """Rasterize ``plan``. GPU failures propagate and do not use the CPU."""

    if backend == "cpu":
        from .cpu import render_png

        result = render_png(plan, None)
        if not result.data:
            raise PreviewError("CPU preview did not return image bytes")
        if image_format == "png":
            return result.data
        return _png_bytes_to_webp(result.data)
    if backend == "gpu":
        from .gpu import render as render_gpu

        result = render_gpu(plan, None)
        if not result.data:
            raise PreviewError("GPU preview did not return image bytes")
        return result.data
    raise PreviewUnsupported(f"unsupported preview backend {backend!r}")


def _png_bytes_to_webp(data: bytes) -> bytes:
    from io import BytesIO

    from PIL import Image, features

    if not features.check("webp"):
        raise PreviewUnsupported(
            "This Pillow build cannot encode WebP.",
            hint="Request format png, or install a Pillow build with WebP.",
        )
    image = Image.open(BytesIO(data))
    buffer = BytesIO()
    image.save(
        buffer,
        format="WEBP",
        quality=WEBP_QUALITY,
        method=WEBP_METHOD,
        lossless=WEBP_LOSSLESS,
    )
    return buffer.getvalue()


def _media_type(image_format: str) -> str:
    if image_format == "png":
        return "image/png"
    if image_format == "webp":
        return "image/webp"
    raise PreviewUnsupported(f"unsupported preview format {image_format!r}")


@dataclass
class _Entry:
    namespace: str
    request_key: str
    plan_sha256: str
    backend: str
    image_format: str
    data: bytes
    width: int
    height: int
    camera: dict[str, Any]
    summary: dict[str, Any]
    warnings: tuple[str, ...]
    ticket: str
    replay: dict[str, Any]
    live_source: Any
    nbytes: int


@dataclass
class _Flight:
    event: threading.Event = field(default_factory=threading.Event)
    result: PreviewImage | None = None
    error: BaseException | None = None


class PreviewCache:
    """Namespace-partitioned raster cache with a byte and entry ceiling.

    A payload larger than ``max_bytes`` is returned to the caller and not
    retained. Eviction drops the oldest retained entry until both limits
    hold, and invalidates that entry's ticket.
    """

    def __init__(
        self,
        *,
        max_entries: int = PREVIEW_CACHE_MAX_ENTRIES,
        max_bytes: int = PREVIEW_CACHE_MAX_BYTES,
    ) -> None:
        if max_entries <= 0 or max_bytes <= 0:
            raise PreviewError("preview cache limits must be positive")
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str], _Entry] = OrderedDict()
        self._tickets: dict[str, tuple[str, str]] = {}
        self._plan_index: dict[tuple[str, str, str, str], str] = {}
        self._inflight: dict[tuple[str, str], _Flight] = {}
        self._bytes = 0

    def render(self, request: PreviewRequest) -> PreviewImage:
        material = request.cache_material()
        if material is None:
            return self._render_live(request)
        request_key = preview_cache_key(material)
        started = time.perf_counter()
        cached = self._lookup(request.namespace, request_key, started)
        if cached is not None:
            return cached
        flight, role = self._join(request.namespace, request_key)
        if role == "cached":
            return self._from_entry(flight, started, status="hit", rasterized=False)  # type: ignore[arg-type]
        if role == "wait":
            return self._wait(flight, started)  # type: ignore[arg-type]
        try:
            image = self._miss(request, request_key)
            flight.result = image  # type: ignore[union-attr]
            return image
        except BaseException as exc:
            flight.error = exc  # type: ignore[union-attr]
            raise
        finally:
            self._finish(request.namespace, request_key, flight)  # type: ignore[arg-type]

    def redeem(self, ticket: str) -> _Entry:
        with self._lock:
            key = self._tickets.get(ticket)
            if key is None:
                raise PreviewNotFound(f"unknown preview ticket {ticket!r}")
            entry = self._entries.get(key)
            if entry is None:
                raise PreviewNotFound(f"unknown preview ticket {ticket!r}")
            self._entries.move_to_end(key)
            return entry

    def _lookup(
        self,
        namespace: str,
        request_key: str,
        started: float,
    ) -> PreviewImage | None:
        with self._lock:
            entry = self._entries.get((namespace, request_key))
            if entry is None:
                return None
            self._entries.move_to_end((namespace, request_key))
            return self._image_from_entry(
                entry,
                status="hit",
                rasterized=False,
                timing=_timing(started, prepare=0.0, render_encode=0.0),
            )

    def _join(
        self,
        namespace: str,
        request_key: str,
    ) -> tuple[_Flight | _Entry, str]:
        with self._lock:
            existing = self._entries.get((namespace, request_key))
            if existing is not None:
                self._entries.move_to_end((namespace, request_key))
                return existing, "cached"
            flight = self._inflight.get((namespace, request_key))
            if flight is not None:
                return flight, "wait"
            flight = _Flight()
            self._inflight[(namespace, request_key)] = flight
            return flight, "owner"

    def _wait(self, flight: _Flight, started: float) -> PreviewImage:
        flight.event.wait()
        if flight.error is not None:
            raise flight.error
        if flight.result is None:
            raise PreviewError("in-flight preview finished without an image")
        elapsed = time.perf_counter() - started
        timing = dict(flight.result.timing_s)
        timing["waited"] = elapsed
        timing["total"] = elapsed
        timing["render_encode"] = 0.0
        timing["prepare"] = 0.0
        return replace(
            flight.result,
            cache_status="coalesced",
            rasterized=False,
            timing_s=timing,
        )

    def _finish(self, namespace: str, request_key: str, flight: _Flight) -> None:
        flight.event.set()
        with self._lock:
            current = self._inflight.get((namespace, request_key))
            if current is flight:
                self._inflight.pop((namespace, request_key), None)

    def _miss(
        self,
        request: PreviewRequest,
        request_key: str,
    ) -> PreviewImage:
        prepared = time.perf_counter()
        plan = request.prepare()
        prepare_s = time.perf_counter() - prepared
        alias = self._aliased_bytes(request, plan)
        if alias is not None:
            data, _entry = alias
            return self._remember(
                request,
                request_key,
                plan,
                data,
                status="hit",
                rasterized=False,
                prepare_s=prepare_s,
                render_encode_s=0.0,
            )
        rendered = time.perf_counter()
        data = encode_preview_raster(
            plan,
            backend=request.backend,
            image_format=request.image_format,
        )
        render_s = time.perf_counter() - rendered
        return self._remember(
            request,
            request_key,
            plan,
            data,
            status="miss",
            rasterized=True,
            prepare_s=prepare_s,
            render_encode_s=render_s,
        )

    def _render_live(self, request: PreviewRequest) -> PreviewImage:
        prepared = time.perf_counter()
        plan = request.prepare()
        prepare_s = time.perf_counter() - prepared
        material = plan_cache_material(
            plan,
            backend=request.backend,
            image_format=request.image_format,
        )
        request_key = preview_cache_key(material)
        started = time.perf_counter() - prepare_s
        cached = self._lookup(request.namespace, request_key, started)
        if cached is not None:
            timing = dict(cached.timing_s)
            timing["prepare"] = prepare_s
            timing["total"] = timing["total"] + prepare_s
            return replace(cached, timing_s=timing)
        rendered = time.perf_counter()
        data = encode_preview_raster(
            plan,
            backend=request.backend,
            image_format=request.image_format,
        )
        render_s = time.perf_counter() - rendered
        return self._remember(
            request,
            request_key,
            plan,
            data,
            status="miss",
            rasterized=True,
            prepare_s=prepare_s,
            render_encode_s=render_s,
        )

    def _aliased_bytes(
        self,
        request: PreviewRequest,
        plan: RenderPlan,
    ) -> tuple[bytes, _Entry] | None:
        index = (
            request.namespace,
            plan.fingerprint(),
            request.backend,
            request.image_format,
        )
        with self._lock:
            request_key = self._plan_index.get(index)
            if request_key is None:
                return None
            entry = self._entries.get((request.namespace, request_key))
            if entry is None:
                self._plan_index.pop(index, None)
                return None
            return entry.data, entry

    def _remember(
        self,
        request: PreviewRequest,
        request_key: str,
        plan: RenderPlan,
        data: bytes,
        *,
        status: str,
        rasterized: bool,
        prepare_s: float,
        render_encode_s: float,
    ) -> PreviewImage:
        warnings = tuple(dict.fromkeys((*plan.warnings, RASTER_NOTE)))
        camera = camera_payload(plan)
        summary = scene_summary(plan)
        timing = {
            "prepare": prepare_s,
            "render_encode": render_encode_s,
            "total": prepare_s + render_encode_s,
        }
        source_material = _source_material(request.source)
        replay = {
            "kind": "live" if source_material is None else "json",
            "source": source_material,
            "request": request.request_record(),
        }
        retained_live = request.source if replay["kind"] == "live" else None
        entry = _Entry(
            namespace=request.namespace,
            request_key=request_key,
            plan_sha256=plan.fingerprint(),
            backend=request.backend,
            image_format=request.image_format,
            data=data,
            width=plan.width,
            height=plan.height,
            camera=camera,
            summary=summary,
            warnings=warnings,
            ticket=secrets.token_urlsafe(32),
            replay=replay,
            live_source=retained_live,
            nbytes=len(data) + len(
                json.dumps(
                    {"kind": replay["kind"], "request": replay["request"]},
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ),
        )
        retained = self._store(entry)
        return self._image_from_entry(
            entry,
            status=status,
            rasterized=rasterized,
            timing=timing,
            retained=retained,
        )

    def _store(self, entry: _Entry) -> bool:
        with self._lock:
            if entry.nbytes > self.max_bytes:
                return False
            key = (entry.namespace, entry.request_key)
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous.nbytes
                self._tickets.pop(previous.ticket, None)
            self._entries[key] = entry
            self._tickets[entry.ticket] = key
            self._bytes += entry.nbytes
            self._plan_index[
                (
                    entry.namespace,
                    entry.plan_sha256,
                    entry.backend,
                    entry.image_format,
                )
            ] = entry.request_key
            self._evict()
            return key in self._entries

    def _evict(self) -> None:
        while self._entries and (
            len(self._entries) > self.max_entries or self._bytes > self.max_bytes
        ):
            _key, entry = self._entries.popitem(last=False)
            self._bytes -= entry.nbytes
            self._tickets.pop(entry.ticket, None)
            index = (
                entry.namespace,
                entry.plan_sha256,
                entry.backend,
                entry.image_format,
            )
            if self._plan_index.get(index) == entry.request_key:
                self._plan_index.pop(index, None)
                for (namespace, request_key), other in reversed(self._entries.items()):
                    if (
                        namespace == entry.namespace
                        and other.plan_sha256 == entry.plan_sha256
                        and other.backend == entry.backend
                        and other.image_format == entry.image_format
                    ):
                        self._plan_index[index] = request_key
                        break

    def _from_entry(
        self,
        entry: _Entry,
        started: float,
        *,
        status: str,
        rasterized: bool,
    ) -> PreviewImage:
        return self._image_from_entry(
            entry,
            status=status,
            rasterized=rasterized,
            timing=_timing(started, prepare=0.0, render_encode=0.0),
        )

    def _image_from_entry(
        self,
        entry: _Entry,
        *,
        status: str,
        rasterized: bool,
        timing: dict[str, float],
        retained: bool = True,
    ) -> PreviewImage:
        return PreviewImage(
            schema=PREVIEW_IMAGE_SCHEMA,
            media_type=_media_type(entry.image_format),
            image_format=entry.image_format,
            backend=entry.backend,
            width=entry.width,
            height=entry.height,
            data=entry.data,
            sha256=sha256(entry.data).hexdigest(),
            cache_key=entry.request_key,
            cache_status=status,
            rasterized=rasterized,
            retained=retained,
            ticket=entry.ticket if retained else None,
            plan_sha256=entry.plan_sha256,
            camera=entry.camera,
            summary=entry.summary,
            warnings=entry.warnings,
            timing_s=timing,
            namespace=entry.namespace,
        )


def _timing(
    started: float, *, prepare: float, render_encode: float
) -> dict[str, float]:
    return {
        "prepare": prepare,
        "render_encode": render_encode,
        "total": time.perf_counter() - started,
    }


def render_preview(
    source: Any,
    *,
    cache: PreviewCache,
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
) -> PreviewImage:
    """Render or reuse a namespace-scoped preview raster."""

    request = build_preview_request(
        source,
        namespace=namespace,
        camera=camera,
        view=view,
        style=style,
        width=width,
        height=height,
        scale=scale,
        backend=backend,
        image_format=image_format,
        atom_groups=atom_groups,
        bond_groups=bond_groups,
        cell_overlays=cell_overlays,
        vector_overlays=vector_overlays,
        topology_data=topology_data,
        atom_property_color=atom_property_color,
    )
    return cache.render(request)


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


def activation_html(
    cache: PreviewCache,
    ticket: str,
    *,
    include_plotlyjs: bool | str = True,
) -> bytes:
    """One complete Plotly document for the retained preview plan.

    The document is built from a freshly prepared plan. The call refuses
    to return HTML when that plan's fingerprint differs from the preview.
    ``include_plotlyjs=True`` inlines Plotly.js. ``"directory"`` references
    a sibling ``plotly.min.js``.
    """

    entry = cache.redeem(ticket)
    plan = _replay_plan(entry)
    if plan.fingerprint() != entry.plan_sha256:
        raise PreviewError(
            "replayed plan does not match the cached preview",
            hint="Request a new preview. The source or renderer changed.",
        )
    html = _plan_html(plan, include_plotlyjs=include_plotlyjs)
    marker = f"<!-- mattervis-preview-plan:{plan.fingerprint()} -->\n".encode()
    return marker + html


def _replay_plan(entry: _Entry) -> RenderPlan:
    record = entry.replay.get("request")
    if not isinstance(record, Mapping):
        raise PreviewError("cached preview is missing its replay request")
    if entry.replay.get("kind") == "live":
        if entry.live_source is None:
            raise PreviewError("live preview source is no longer retained")
        source: Any = entry.live_source
    else:
        source = _source_from_record(entry.replay.get("source"))
    request = PreviewRequest(
        namespace=entry.namespace,
        source=source,
        camera=record.get("camera"),
        view=record.get("view"),
        style=record.get("style"),
        width=int(record.get("width", entry.width)),
        height=int(record.get("height", entry.height)),
        scale=int(record.get("scale", DEFAULT_PREVIEW_SCALE)),
        backend=entry.backend,
        image_format=entry.image_format,
        atom_groups=record.get("atom_groups"),
        bond_groups=record.get("bond_groups"),
        cell_overlays=record.get("cell_overlays"),
        vector_overlays=record.get("vector_overlays"),
        topology_data=record.get("topology_data"),
        atom_property_color=record.get("atom_property_color"),
    )
    return request.prepare()


def _source_from_record(source: Any) -> Any:
    if not isinstance(source, Mapping):
        raise PreviewError("cached preview source is missing")
    kind = source.get("kind")
    if kind == "scene":
        scene = source.get("scene")
        if not isinstance(scene, Mapping):
            raise PreviewError("cached scene preview is missing")
        return json_ready(scene)
    if kind in {"cif", "xyz"}:
        return TextStructure(fmt=str(kind), text=str(source.get("text") or ""))
    if kind == "path":
        return str(source.get("path"))
    raise PreviewError(f"cannot replay preview source kind {kind!r}")


def _plan_html(plan: RenderPlan, *, include_plotlyjs: bool | str) -> bytes:
    try:
        from .plotly import build_figure, interactive_document
    except ImportError as exc:
        from ..capabilities import resolve_requirements

        install = resolve_requirements("plotly").install_command
        raise PreviewUnsupported(
            "Activating a preview requires the Plotly extra.",
            hint=f"Install with: {install}",
        ) from exc
    figure = build_figure(plan, interactive=True)
    return interactive_document(figure, include_plotlyjs=include_plotlyjs)


def write_shared_plotlyjs(directory: Path) -> Path:
    """Write one local Plotly.js bundle. The file is not a CDN URL."""

    try:
        from plotly.offline import get_plotlyjs
    except ImportError as exc:
        from ..capabilities import resolve_requirements

        install = resolve_requirements("plotly").install_command
        raise PreviewUnsupported(
            "A shared Plotly.js file requires the Plotly extra.",
            hint=f"Install with: {install}",
        ) from exc
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "plotly.min.js"
    path.write_text(get_plotlyjs(), encoding="utf-8")
    return path


def write_preview_host(
    directory: str | Path,
    images: Sequence[PreviewImage],
    *,
    cache: PreviewCache,
    activate: bool = True,
) -> Path:
    """Write a host page that shows rasters and can mount one viewer.

    Inactive panels are ``<img>`` elements. An activation control sets one
    iframe ``src`` to a sibling HTML document that references
    ``plotly.min.js``. The host document does not call ``Plotly.newPlot``.
    """

    if not images:
        raise PreviewError("preview host needs at least one image")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    panels: list[str] = []
    if activate:
        write_shared_plotlyjs(root)
    for index, image in enumerate(images):
        suffix = image.image_format
        filename = f"panel-{index}.{suffix}"
        (root / filename).write_bytes(image.data)
        frame = ""
        button = ""
        if activate:
            if not image.ticket or not image.retained:
                raise PreviewError(
                    "activation requires a retained preview ticket",
                    hint="Raise the preview cache budget or request a smaller image.",
                )
            document = activation_html(
                cache,
                image.ticket,
                include_plotlyjs="directory",
            )
            (root / f"panel-{index}.html").write_bytes(document)
            frame = (
                f'<iframe data-src="panel-{index}.html" title="3D viewer {index}"'
                f' hidden></iframe>'
            )
            button = (
                f'<button type="button" data-activate="{index}">Inspect in 3D</button>'
                f'<button type="button" data-close="{index}" hidden>Close 3D</button>'
            )
        panels.append(
            f'<section class="panel" data-panel="{index}">'
            f'<img alt="Static structure preview {index}" src="{filename}" '
            f'width="{image.width}" height="{image.height}">'
            f"{frame}{button}</section>"
        )
    script = _HOST_SCRIPT if activate else ""
    blurb = (
        "Raster previews are static images. They are not a continuous 3D viewer."
    )
    if activate:
        blurb += (
            " Inspect in 3D loads one Plotly viewer "
            f"(maximum {MAX_ACTIVE_VIEWERS})."
        )
    html = (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>MatterVis previews</title>\n<style>{_HOST_CSS}</style>\n"
        "</head>\n<body>\n"
        f"<p data-mattervis-preview=\"static\">{blurb}</p>\n"
        + "\n".join(panels)
        + "\n"
        + script
        + "\n</body>\n</html>\n"
    )
    path = root / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def write_camera_swap_page(
    path: str | Path,
    frames: Sequence[tuple[str, bytes]],
    *,
    width: int,
    height: int,
) -> Path:
    """Write a page that replaces one image when the camera changes.

    The document has no Plotly script, no iframe, and no canvas.
    """

    if not frames:
        raise PreviewError("camera swap page needs at least one image")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    buttons: list[str] = []
    for index, (label, data) in enumerate(frames):
        filename = f"{destination.stem}-{index}.png"
        (destination.parent / filename).write_bytes(data)
        safe_label = (
            str(label)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        buttons.append(
            f'<button type="button" data-frame="{filename}">{safe_label}</button>'
        )
    first = f"{destination.stem}-0.png"
    html = (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>MatterVis camera previews</title></head><body>\n"
        "<p>Choosing a camera replaces the image. This page does not create "
        "a WebGL context.</p>\n"
        f"<img id=\"preview\" alt=\"Static structure preview\" src=\"{first}\" "
        f"width=\"{int(width)}\" height=\"{int(height)}\">\n"
        + "\n".join(buttons)
        + "\n<script>\n"
        "(function () {\n"
        "  var image = document.getElementById(\"preview\");\n"
        "  document.addEventListener(\"click\", function (event) {\n"
        "    var frame = event.target.getAttribute && "
        "event.target.getAttribute(\"data-frame\");\n"
        "    if (!frame) return;\n"
        "    image.src = frame;\n"
        "  });\n"
        "})();\n"
        "</script>\n</body></html>\n"
    )
    destination.write_text(html, encoding="utf-8")
    return destination


_HOST_CSS = (
    "body{font-family:sans-serif;margin:16px;}"
    ".panel{position:relative;display:inline-block;margin:0 12px 12px 0;}"
    ".panel img,.panel iframe{display:block;border:1px solid #ccc;background:#fff;}"
    ".panel iframe{position:absolute;inset:0;width:100%;height:100%;}"
    "button{margin:4px 4px 0 0;}"
)

_HOST_SCRIPT = """<script>
(function () {
  "use strict";
  var maxActive = __MAX_ACTIVE__;
  var active = [];
  function panel(index) {
    return document.querySelector('[data-panel="' + index + '"]');
  }
  function closeViewer(index) {
    var root = panel(index);
    if (!root) return;
    var frame = root.querySelector("iframe");
    var image = root.querySelector("img");
    if (frame) {
      frame.hidden = true;
      frame.src = "about:blank";
      frame.removeAttribute("src");
    }
    if (image) image.hidden = false;
    var close = root.querySelector("[data-close]");
    if (close) close.hidden = true;
    var at = active.indexOf(index);
    if (at !== -1) active.splice(at, 1);
  }
  function activate(index) {
    if (active.indexOf(index) !== -1) return;
    while (active.length >= maxActive) closeViewer(active[0]);
    var root = panel(index);
    var frame = root.querySelector("iframe");
    var image = root.querySelector("img");
    if (!frame || !frame.getAttribute("data-src")) return;
    frame.src = frame.getAttribute("data-src");
    frame.hidden = false;
    if (image) image.hidden = true;
    var close = root.querySelector("[data-close]");
    if (close) close.hidden = false;
    active.push(index);
  }
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.getAttribute) return;
    var open = target.getAttribute("data-activate");
    if (open !== null) activate(open);
    var shut = target.getAttribute("data-close");
    if (shut !== null) closeViewer(shut);
  });
})();
</script>""".replace("__MAX_ACTIVE__", str(MAX_ACTIVE_VIEWERS))


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


__all__ = [
    "DEFAULT_PREVIEW_HEIGHT",
    "DEFAULT_PREVIEW_SCALE",
    "DEFAULT_PREVIEW_WIDTH",
    "MAX_ACTIVE_VIEWERS",
    "PREVIEW_CACHE_SCHEMA",
    "PREVIEW_IMAGE_SCHEMA",
    "RASTER_NOTE",
    "TIMING_NOTE",
    "PreviewCache",
    "PreviewError",
    "PreviewImage",
    "PreviewNotFound",
    "PreviewRequest",
    "PreviewUnsupported",
    "TextStructure",
    "activation_html",
    "build_preview_request",
    "encode_preview_raster",
    "encoder_descriptor",
    "json_ready",
    "preview_cache_key",
    "preview_request_from_payload",
    "render_preview",
    "scene_summary",
    "synthetic_ch_pair_grid",
    "write_camera_swap_page",
    "write_preview_host",
    "write_shared_plotlyjs",
]
