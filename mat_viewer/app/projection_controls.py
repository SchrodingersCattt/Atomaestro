"""Scene-scoped projection radio reconciliation for the Dash callback owner."""

import threading

from dash import no_update

from .camera_helpers import _coerce_projection

# Tickets are minted in the browser at pointerdown, one per radio click.
# A slow orthographic commit must not land after the later perspective click.
_TICKET_LOCK = threading.Lock()
_PROJECTION_TICKETS: dict[str, int] = {}


def triggered_component_ids(context):
    """Collect a whole trigger batch, including a simultaneous tab change."""
    triggered_ids = {
        str(item["prop_id"]).split(".")[0]
        for item in getattr(context, "triggered", None) or []
        if isinstance(item, dict) and item.get("prop_id")
    }
    return triggered_ids or {getattr(context, "triggered_id", None)}


def selected_scene_projection(backend, state, scene_tab, *, tab_changed):
    """Read the selected scene's live projection, fencing unrelated stores."""
    if not scene_tab or scene_tab not in backend.scene_store.scenes:
        return no_update
    if not tab_changed and (
        not isinstance(state, dict) or str(state.get("scene_id") or "") != str(scene_tab)
    ):
        return no_update
    # The payload may predate a newer camera command on the same scene.
    live = backend.get_state(scene_tab)
    return _coerce_projection(live.get("projection") or "perspective")


def projection_command(ticket, projection):
    """Return the clicked projection and its ticket.

    A ticket store payload wins over the radio value so the command that
    was current at pointerdown is the one the server orders.
    """

    if isinstance(ticket, dict):
        return ticket.get("projection"), ticket.get("ticket")
    return projection, None


def accept_projection_ticket(scene_id, ticket) -> bool:
    """Keep the newest radio click for this scene.

    An unversioned caller (tests and server-side projection changes) is
    always accepted and does not erase a browser ticket. An older ticket
    than one already accepted for the scene is ignored.
    """

    if ticket is None or not scene_id:
        return True
    try:
        current = int(ticket)
    except (TypeError, ValueError):
        return True
    key = str(scene_id)
    with _TICKET_LOCK:
        previous = _PROJECTION_TICKETS.get(key)
        if previous is not None and current < previous:
            return False
        _PROJECTION_TICKETS[key] = current
        return True
