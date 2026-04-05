import json
import threading
from pathlib import Path
from typing import Any

from .models import ensure_route
from .time_utils import iso_to_epoch, utc_now_iso


class RouteStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, route_id: str) -> Path:
        safe_id = "".join(character for character in route_id if character.isalnum() or character in ("-", "_"))
        return self.root / f"{safe_id}.json"

    def list_routes(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        routes: list[dict[str, Any]] = []
        with self._lock:
            for child in self.root.glob("*.json"):
                try:
                    route = json.loads(child.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not include_deleted and route.get("deletedAt"):
                    continue
                routes.append(route)
        routes.sort(key=lambda item: (item.get("name", "").lower(), item.get("id", "")))
        return routes

    def get_route(self, route_id: str, include_deleted: bool = False) -> dict[str, Any] | None:
        path = self._path(route_id)
        if not path.exists():
            return None
        try:
            route = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if route.get("deletedAt") and not include_deleted:
            return None
        return route

    def save_route(self, route: dict[str, Any]) -> dict[str, Any]:
        normalized = ensure_route(route, self.get_route(route["id"], include_deleted=True))
        with self._lock:
            self._path(normalized["id"]).write_text(json.dumps(normalized, indent=2), encoding="utf-8")
        return normalized

    def tombstone_route(self, route_id: str, deleted_at: str | None = None) -> dict[str, Any] | None:
        existing = self.get_route(route_id, include_deleted=True)
        if not existing:
            return None
        timestamp = deleted_at or utc_now_iso()
        updated = {
            **existing,
            "updatedAt": timestamp,
            "deletedAt": timestamp,
        }
        return self.save_route(updated)

    def sync_routes(self, routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for incoming in routes:
            normalized = ensure_route(incoming, self.get_route(str(incoming.get("id")), include_deleted=True))
            existing = self.get_route(normalized["id"], include_deleted=True)
            if not existing or iso_to_epoch(normalized["updatedAt"]) >= iso_to_epoch(existing.get("updatedAt")):
                self.save_route(normalized)
        return self.list_routes(include_deleted=True)
