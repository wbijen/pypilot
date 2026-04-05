import uuid
from dataclasses import dataclass
from typing import Any

from .geo import clamp
from .time_utils import utc_now_iso


def generate_route_id() -> str:
    return f"route-{uuid.uuid4().hex[:12]}"


def ensure_waypoint(payload: dict[str, Any], index: int) -> dict[str, Any]:
    lat = float(payload["lat"])
    lon = float(payload["lon"])
    arrival_radius = float(payload.get("arrivalRadiusM", 20.0))
    advance_mode = str(payload.get("advanceMode", "radius_or_passed_perpendicular"))
    if advance_mode not in ("radius", "radius_or_passed_perpendicular"):
        advance_mode = "radius_or_passed_perpendicular"
    return {
        "id": str(payload.get("id") or f"wp-{uuid.uuid4().hex[:10]}"),
        "name": str(payload.get("name") or f"WP{index + 1}"),
        "lat": lat,
        "lon": lon,
        "arrivalRadiusM": clamp(arrival_radius, 1.0, 5000.0),
        "advanceMode": advance_mode,
    }


def ensure_route(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    now = utc_now_iso()
    waypoints = [ensure_waypoint(item, index) for index, item in enumerate(payload.get("waypoints", []))]
    route_id = str(payload.get("id") or existing and existing.get("id") or generate_route_id())
    created_at = str(payload.get("createdAt") or existing and existing.get("createdAt") or now)
    updated_at = str(payload.get("updatedAt") or now)
    deleted_at = payload.get("deletedAt")
    deleted_at = str(deleted_at) if deleted_at else None
    return {
        "id": route_id,
        "name": str(payload.get("name") or existing and existing.get("name") or "Untitled Route"),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "deletedAt": deleted_at,
        "isLoop": bool(payload.get("isLoop", existing.get("isLoop") if existing else False)),
        "waypoints": waypoints,
    }


@dataclass
class PypilotSnapshot:
    connected: bool = False
    gps_fix: dict[str, Any] | None = None
    gps_source: str = "none"
    ap_mode: str = "compass"
    ap_enabled: bool = False
