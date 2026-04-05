from typing import Any

from flask import Flask, jsonify, request

from .manager import RouteNavigationManager


def create_app(manager: RouteNavigationManager) -> Flask:
    app = Flask(__name__)

    @app.get("/api/routes")
    def list_routes() -> Any:
        return jsonify({"ok": True, "routes": manager.list_routes()})

    @app.post("/api/routes")
    def create_route() -> Any:
        payload = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "route": manager.create_route(payload)}), 201

    @app.get("/api/routes/<path:route_id>")
    def get_route(route_id: str) -> Any:
        route = manager.get_route(route_id)
        if not route:
            return jsonify({"ok": False, "message": f"Unknown route: {route_id}"}), 404
        return jsonify({"ok": True, "route": route})

    @app.put("/api/routes/<path:route_id>")
    def update_route(route_id: str) -> Any:
        route = manager.update_route(route_id, request.get_json(silent=True) or {})
        if not route:
            return jsonify({"ok": False, "message": f"Unknown route: {route_id}"}), 404
        return jsonify({"ok": True, "route": route})

    @app.delete("/api/routes/<path:route_id>")
    def delete_route(route_id: str) -> Any:
        route = manager.delete_route(route_id)
        if not route:
            return jsonify({"ok": False, "message": f"Unknown route: {route_id}"}), 404
        return jsonify({"ok": True, "route": route})

    @app.post("/api/routes/sync")
    def sync_routes() -> Any:
        try:
            return jsonify(manager.sync_routes(request.get_json(silent=True) or {}))
        except ValueError as error:
            return jsonify({"ok": False, "message": str(error)}), 400

    @app.get("/api/routes/sync-state")
    def sync_state() -> Any:
        return jsonify(manager.sync_state())

    @app.post("/api/routes/<path:route_id>/activate")
    def activate_route(route_id: str) -> Any:
        try:
            status = manager.activate_route(route_id)
        except ValueError as error:
            return jsonify({"ok": False, "message": str(error)}), 400
        if not status:
            return jsonify({"ok": False, "message": f"Unknown route: {route_id}"}), 404
        return jsonify(status)

    @app.post("/api/routes/active/stop")
    def stop_route() -> Any:
        return jsonify(manager.stop_active_route())

    @app.post("/api/routes/active/resume")
    def resume_route() -> Any:
        try:
            return jsonify(manager.resume_active_route())
        except ValueError as error:
            return jsonify({"ok": False, "message": str(error)}), 400

    @app.get("/api/routes/active/status")
    def active_status() -> Any:
        return jsonify(manager.active_status())

    @app.get("/api/routes/health")
    def health() -> Any:
        return jsonify(manager.health())

    return app
