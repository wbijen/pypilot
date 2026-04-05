import os
from pathlib import Path

from .app import create_app
from .manager import RouteNavigationManager


def main() -> None:
    route_root = Path(os.getenv("PYPILOT_ROUTE_ROOT", str(Path.home() / ".pypilot" / "routes")))
    state_path = Path(os.getenv("PYPILOT_ROUTE_STATE", str(Path.home() / ".pypilot" / "route-nav-state.json")))
    port = int(os.getenv("PYPILOT_ROUTE_SERVICE_PORT", "20221"))
    manager = RouteNavigationManager(route_root=route_root, state_path=state_path)
    manager.start()
    app = create_app(manager)
    try:
        app.run(host="0.0.0.0", port=port, debug=False)
    finally:
        manager.shutdown()
