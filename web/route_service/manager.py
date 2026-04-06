import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

from pypilot.client import pypilotClient

from .constants import METERS_PER_NM
from .geo import bearing_true_deg, haversine_distance_m, leg_metrics_m
from .models import PypilotSnapshot, ensure_route
from .nmea import apb_sentence
from .store import RouteStore
from .time_utils import utc_now_iso


class RouteNavigationManager:
    def __init__(
        self,
        route_root: Path,
        state_path: Path,
        pypilot_host: str | None = None,
        nmea_host: str = "127.0.0.1",
        nmea_port: int = 20220,
    ):
        self.store = RouteStore(route_root)
        self.state_path = state_path
        self.snapshot = PypilotSnapshot()
        self.client = pypilotClient(pypilot_host or False)
        self.nmea_host = nmea_host
        self.nmea_port = nmea_port
        self._nmea_socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._watches_registered = False
        self._active = self._load_state()
        self._last_apb_sent_at = 0.0
        self._last_nmea_error = ""
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)

    def _read_gpsd_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "ok": False,
            "connected": False,
            "deviceCount": 0,
            "devicePath": None,
            "mode": 0,
            "fixType": "none",
            "satellitesVisible": None,
            "satellitesUsed": None,
            "status": "unavailable",
            "error": "",
        }
        connection: socket.socket | None = None
        try:
            connection = socket.create_connection(("127.0.0.1", 2947), timeout=1.0)
            connection.settimeout(1.0)
            connection.sendall(b'?WATCH={"enable":true,"json":true};')
            buffer = ""
            start = time.monotonic()
            device_path = None
            mode = 0
            satellites_visible = None
            satellites_used = None
            device_count = 0
            while time.monotonic() - start < 2.0:
                try:
                    chunk = connection.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", "replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except Exception:
                        continue
                    cls = message.get("class")
                    if cls == "DEVICES":
                        devices = message.get("devices")
                        if isinstance(devices, list):
                            device_count = len(devices)
                            if devices and isinstance(devices[0], dict):
                                device_path = devices[0].get("path")
                    elif cls == "DEVICE":
                        device_path = message.get("path") or device_path
                        if device_path and device_count == 0:
                            device_count = 1
                    elif cls == "TPV":
                        try:
                            mode = max(mode, int(message.get("mode", 0)))
                        except Exception:
                            pass
                        if message.get("device"):
                            device_path = message.get("device")
                    elif cls == "SKY":
                        satellites = message.get("satellites")
                        if isinstance(satellites, list):
                            satellites_visible = len(satellites)
                            satellites_used = len(
                                [satellite for satellite in satellites if isinstance(satellite, dict) and satellite.get("used")]
                            )
                if mode >= 2 and satellites_visible is not None:
                    break

            fix_type = "none"
            if mode >= 3:
                fix_type = "3d"
            elif mode >= 2:
                fix_type = "2d"
            elif device_count > 0:
                fix_type = "no_fix"

            status = "no_device"
            if device_count > 0:
                status = "fix" if mode >= 2 else "no_fix"

            summary.update(
                {
                    "ok": True,
                    "connected": True,
                    "deviceCount": device_count,
                    "devicePath": device_path,
                    "mode": mode,
                    "fixType": fix_type,
                    "satellitesVisible": satellites_visible,
                    "satellitesUsed": satellites_used,
                    "status": status,
                }
            )
        except Exception as error:
            summary["error"] = str(error)
        finally:
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass
        return summary

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._close_nmea_socket()
        self.client.disconnect()

    def _default_state(self) -> dict[str, Any]:
        return {
            "routeId": None,
            "state": "idle",
            "waypointIndex": 0,
            "message": "No active route.",
            "updatedAt": utc_now_iso(),
            "distanceToWaypointM": None,
            "bearingTrueDeg": None,
            "desiredTrackTrueDeg": None,
            "xteNm": None,
            "arrivalRadiusM": None,
            "advanceMode": None,
            "legFrom": None,
            "legTo": None,
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return self._default_state()
        merged = self._default_state()
        merged.update(state if isinstance(state, dict) else {})
        return merged

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._active, indent=2), encoding="utf-8")

    def _ensure_client(self) -> bool:
        try:
            if getattr(self.client, "connection", False):
                self.snapshot.connected = True
            else:
                self.snapshot.connected = bool(self.client.connect(verbose=False))
        except Exception as error:
            self.snapshot.connected = False
            self._last_nmea_error = f"pypilot connect failed: {error}"
            return False
        if self.snapshot.connected and not self._watches_registered:
            for name in ("gps.fix", "gps.source", "ap.mode", "ap.enabled"):
                self.client.watch(name, True)
            self._watches_registered = True
        return self.snapshot.connected

    def _poll_snapshot(self) -> None:
        if not self._ensure_client():
            self.snapshot.connected = False
            return
        messages = self.client.receive(timeout=0)
        if "gps.fix" in messages:
            gps_fix = messages["gps.fix"]
            self.snapshot.gps_fix = gps_fix if isinstance(gps_fix, dict) else None
        if "gps.source" in messages:
            self.snapshot.gps_source = str(messages["gps.source"])
        if "ap.mode" in messages:
            self.snapshot.ap_mode = str(messages["ap.mode"])
        if "ap.enabled" in messages:
            self.snapshot.ap_enabled = bool(messages["ap.enabled"])

    def _get_position(self) -> tuple[float, float] | None:
        gps_fix = self.snapshot.gps_fix
        if self.snapshot.gps_source == "none" or not isinstance(gps_fix, dict):
            return None
        try:
            return float(gps_fix["lat"]), float(gps_fix["lon"])
        except Exception:
            return None

    def _close_nmea_socket(self) -> None:
        if not self._nmea_socket:
            return
        try:
            self._nmea_socket.close()
        except Exception:
            pass
        finally:
            self._nmea_socket = None

    def _ensure_nmea_socket(self) -> socket.socket:
        if self._nmea_socket:
            return self._nmea_socket
        connection = socket.create_connection((self.nmea_host, self.nmea_port), timeout=1.0)
        connection.settimeout(1.0)
        self._nmea_socket = connection
        return connection

    def _write_nmea(self, sentence: str) -> None:
        try:
            connection = self._ensure_nmea_socket()
            connection.sendall((sentence + "\r\n").encode("ascii"))
            self._last_apb_sent_at = time.time()
            self._last_nmea_error = ""
        except Exception as error:
            self._close_nmea_socket()
            self._last_nmea_error = str(error)

    def _set_nav_mode(self) -> None:
        if not self._ensure_client():
            return
        if self.snapshot.ap_mode != "nav":
            self.client.set("ap.mode", "nav")
            self.client.poll(0)

    def list_routes(self) -> list[dict[str, Any]]:
        return self.store.list_routes(include_deleted=False)

    def get_route(self, route_id: str) -> dict[str, Any] | None:
        return self.store.get_route(route_id, include_deleted=False)

    def create_route(self, payload: dict[str, Any]) -> dict[str, Any]:
        route = ensure_route(payload)
        if not route.get("updatedAt"):
            route["updatedAt"] = utc_now_iso()
        return self.store.save_route(route)

    def update_route(self, route_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        existing = self.store.get_route(route_id, include_deleted=True)
        if not existing:
            return None
        route = ensure_route({**existing, **payload, "id": route_id, "updatedAt": utc_now_iso()}, existing)
        return self.store.save_route(route)

    def delete_route(self, route_id: str) -> dict[str, Any] | None:
        deleted = self.store.tombstone_route(route_id)
        with self._lock:
            if self._active.get("routeId") == route_id:
                self._active = self._default_state()
                self._save_state()
        return deleted

    def sync_routes(self, payload: dict[str, Any]) -> dict[str, Any]:
        routes = payload.get("routes")
        if not isinstance(routes, list):
            raise ValueError("routes payload must be a list")
        merged = self.store.sync_routes([item for item in routes if isinstance(item, dict)])
        return {
            "ok": True,
            "routes": merged,
            "serverTime": utc_now_iso(),
            "count": len([item for item in merged if not item.get("deletedAt")]),
        }

    def sync_state(self) -> dict[str, Any]:
        routes = self.store.list_routes(include_deleted=True)
        return {
            "ok": True,
            "serverTime": utc_now_iso(),
            "routeCount": len([item for item in routes if not item.get("deletedAt")]),
            "tombstoneCount": len([item for item in routes if item.get("deletedAt")]),
            "activeRouteId": self._active.get("routeId"),
        }

    def activate_route(self, route_id: str) -> dict[str, Any] | None:
        route = self.store.get_route(route_id)
        if not route:
            return None
        if not route.get("waypoints"):
            raise ValueError("Route must contain at least one waypoint.")
        start_index = self._find_nearest_waypoint_index(route)
        with self._lock:
            self._active.update(
                {
                    "routeId": route_id,
                    "state": "ready",
                    "waypointIndex": start_index,
                    "message": "Route activated. Waiting for GPS fix.",
                    "updatedAt": utc_now_iso(),
                }
            )
            self._save_state()
        self._set_nav_mode()
        return self.active_status()

    def _find_nearest_waypoint_index(self, route: dict[str, Any]) -> int:
        """Find the first waypoint the boat has not yet passed.

        Walks the route sequentially and checks each leg with a
        perpendicular test.  Returns the index of the first waypoint
        whose leg the boat is still approaching or on.
        Falls back to index 0 when GPS is unavailable.
        """
        position = self._get_position()
        if not position:
            return 0
        waypoints = route.get("waypoints", [])
        if len(waypoints) < 2:
            return 0
        # Walk each leg: if the boat is past the perpendicular of
        # waypoint[i] toward waypoint[i+1], it has passed WP[i].
        for i in range(len(waypoints) - 1):
            wp = waypoints[i]
            next_wp = waypoints[i + 1]
            leg_start = (float(wp["lat"]), float(wp["lon"]))
            leg_end = (float(next_wp["lat"]), float(next_wp["lon"]))
            _, along, leg_len = leg_metrics_m(leg_start, leg_end, position)
            if leg_len > 0 and along < leg_len:
                # Boat has NOT yet passed the end of this leg,
                # so it should navigate toward waypoint[i+1].
                return i + 1
        # Boat is past all legs — target the last waypoint
        return len(waypoints) - 1

    def stop_active_route(self) -> dict[str, Any]:
        with self._lock:
            if self._active.get("routeId"):
                self._active["state"] = "paused"
                self._active["message"] = "Route paused."
                self._active["updatedAt"] = utc_now_iso()
            else:
                self._active = self._default_state()
            self._save_state()
        return self.active_status()

    def resume_active_route(self) -> dict[str, Any]:
        with self._lock:
            if not self._active.get("routeId"):
                raise ValueError("No paused route is available to resume.")
            if self._active.get("state") == "completed":
                raise ValueError("Completed routes cannot be resumed.")
            self._active["state"] = "ready"
            self._active["message"] = "Route resume requested. Waiting for GPS fix."
            self._active["updatedAt"] = utc_now_iso()
            self._save_state()
        self._set_nav_mode()
        return self.active_status()

    def active_status(self) -> dict[str, Any]:
        route = None
        waypoint = None
        if self._active.get("routeId"):
            route = self.store.get_route(self._active["routeId"], include_deleted=False)
            if route and route.get("waypoints"):
                nav_waypoints = list(route["waypoints"])
                if route.get("isLoop") and len(nav_waypoints) > 1:
                    nav_waypoints = [*nav_waypoints, route["waypoints"][0]]
                index = int(self._active.get("waypointIndex") or 0)
                if 0 <= index < len(nav_waypoints):
                    waypoint = nav_waypoints[index]
        return {
            "ok": True,
            "routeId": self._active.get("routeId"),
            "state": self._active.get("state"),
            "waypointIndex": self._active.get("waypointIndex"),
            "legFrom": self._active.get("legFrom"),
            "legTo": self._active.get("legTo"),
            "distanceToWaypointM": self._active.get("distanceToWaypointM"),
            "bearingTrueDeg": self._active.get("bearingTrueDeg"),
            "desiredTrackTrueDeg": self._active.get("desiredTrackTrueDeg"),
            "xteNm": self._active.get("xteNm"),
            "arrivalRadiusM": waypoint.get("arrivalRadiusM") if waypoint else self._active.get("arrivalRadiusM"),
            "advanceMode": waypoint.get("advanceMode") if waypoint else self._active.get("advanceMode"),
            "gpsFixOk": self._get_position() is not None,
            "apMode": self.snapshot.ap_mode,
            "apEnabled": self.snapshot.ap_enabled,
            "lastApbSentAt": self._last_apb_sent_at,
            "isLoop": bool(route.get("isLoop")) if route else False,
            "message": self._active.get("message"),
        }

    def health(self) -> dict[str, Any]:
        routes = self.store.list_routes(include_deleted=True)
        gpsd = self._read_gpsd_summary()
        return {
            "ok": True,
            "routeCount": len([item for item in routes if not item.get("deletedAt")]),
            "tombstoneCount": len([item for item in routes if item.get("deletedAt")]),
            "pypilotConnected": self.snapshot.connected,
            "gpsFixOk": self._get_position() is not None,
            "gpsSource": self.snapshot.gps_source,
            "nmeaHealthy": not self._last_nmea_error,
            "lastNmeaError": self._last_nmea_error,
            "activeRoute": self._active.get("routeId"),
            "activeState": self._active.get("state"),
            "gpsd": gpsd,
        }

    def _complete_route(self) -> None:
        self._active["state"] = "completed"
        self._active["message"] = "Route completed."
        self._active["updatedAt"] = utc_now_iso()
        self._active["distanceToWaypointM"] = 0.0
        self._active["xteNm"] = 0.0
        self._save_state()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_snapshot()
                self._tick_active_route()
            except Exception as error:
                with self._lock:
                    if self._active.get("routeId"):
                        self._active["state"] = "error"
                        self._active["message"] = f"Route service error: {error}"
                        self._active["updatedAt"] = utc_now_iso()
                        self._save_state()
            time.sleep(0.5)

    def _tick_active_route(self) -> None:
        with self._lock:
            state = dict(self._active)
        route_id = state.get("routeId")
        if not route_id:
            return
        if state.get("state") in ("paused", "completed", "idle"):
            return
        route = self.store.get_route(route_id, include_deleted=False)
        if not route or not route.get("waypoints"):
            with self._lock:
                self._active["state"] = "error"
                self._active["message"] = f"Route unavailable: {route_id}"
                self._active["updatedAt"] = utc_now_iso()
                self._save_state()
            return
        position = self._get_position()
        if not position:
            with self._lock:
                self._active["state"] = "ready"
                self._active["message"] = "Waiting for GPS fix."
                self._active["updatedAt"] = utc_now_iso()
                self._save_state()
            return

        self._set_nav_mode()
        waypoint_index = int(state.get("waypointIndex") or 0)
        waypoints = list(route["waypoints"])
        if route.get("isLoop") and len(waypoints) > 1:
            waypoints = [*waypoints, route["waypoints"][0]]
        while waypoint_index < len(waypoints):
            waypoint = waypoints[waypoint_index]
            target = (float(waypoint["lat"]), float(waypoint["lon"]))
            first_leg = waypoint_index == 0
            if first_leg:
                leg_start = position
                cross_track_m = 0.0
                along_track_m = 0.0
                leg_length_m = haversine_distance_m(position, target)
            else:
                previous = waypoints[waypoint_index - 1]
                leg_start = (float(previous["lat"]), float(previous["lon"]))
                cross_track_m, along_track_m, leg_length_m = leg_metrics_m(leg_start, target, position)
            distance_m = haversine_distance_m(position, target)
            desired_track_deg = bearing_true_deg(leg_start, target)
            bearing_deg = bearing_true_deg(position, target)
            arrival_radius_m = float(waypoint["arrivalRadiusM"])
            advance_mode = waypoint["advanceMode"]
            should_advance = distance_m <= arrival_radius_m
            if (
                not first_leg
                and advance_mode == "radius_or_passed_perpendicular"
                and leg_length_m > 0.0
                and along_track_m >= leg_length_m
            ):
                should_advance = True
            if should_advance:
                waypoint_index += 1
                with self._lock:
                    self._active["waypointIndex"] = waypoint_index
                    self._active["updatedAt"] = utc_now_iso()
                    self._active["message"] = f"Advanced past {waypoint['name']}."
                    self._save_state()
                if waypoint_index >= len(waypoints):
                    if route.get("isLoop") and len(route.get("waypoints", [])) > 1:
                        waypoint_index = 0
                        with self._lock:
                            self._active["waypointIndex"] = 0
                            self._active["message"] = "Loop restarted."
                            self._active["updatedAt"] = utc_now_iso()
                            self._save_state()
                        continue
                    with self._lock:
                        self._complete_route()
                    return
                continue

            xte_nm = cross_track_m / METERS_PER_NM
            sentence = apb_sentence(desired_track_deg, xte_nm, waypoint["name"], bearing_deg)
            self._write_nmea(sentence)
            with self._lock:
                self._active["state"] = "active"
                self._active["waypointIndex"] = waypoint_index
                self._active["message"] = f"Tracking {waypoint['name']}."
                self._active["updatedAt"] = utc_now_iso()
                self._active["legFrom"] = None if first_leg else waypoints[waypoint_index - 1]["name"]
                self._active["legTo"] = waypoint["name"]
                self._active["distanceToWaypointM"] = round(distance_m, 1)
                self._active["bearingTrueDeg"] = round(bearing_deg, 1)
                self._active["desiredTrackTrueDeg"] = round(desired_track_deg, 1)
                self._active["xteNm"] = round(xte_nm, 4)
                self._active["arrivalRadiusM"] = arrival_radius_m
                self._active["advanceMode"] = advance_mode
                self._save_state()
            return
