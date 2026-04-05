import tempfile
import unittest
from pathlib import Path
from unittest import mock

from web.route_service import RouteNavigationManager, apb_sentence, bearing_true_deg, create_app, haversine_distance_m, leg_metrics_m


class RouteServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.manager = RouteNavigationManager(route_root=root / "routes", state_path=root / "state.json")
        self.app = create_app(self.manager).test_client()

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.tempdir.cleanup()

    def route_payload(self):
        return {
            "id": "demo-route",
            "name": "Demo",
            "createdAt": "2026-04-02T00:00:00Z",
            "updatedAt": "2026-04-02T00:00:00Z",
            "isLoop": False,
            "waypoints": [
                {
                    "id": "wp-1",
                    "name": "A",
                    "lat": 52.0,
                    "lon": 4.0,
                    "arrivalRadiusM": 20,
                    "advanceMode": "radius_or_passed_perpendicular",
                },
                {
                    "id": "wp-2",
                    "name": "B",
                    "lat": 52.001,
                    "lon": 4.001,
                    "arrivalRadiusM": 20,
                    "advanceMode": "radius_or_passed_perpendicular",
                },
            ],
        }

    def test_route_crud_round_trip(self):
        response = self.app.post("/api/routes", json=self.route_payload())
        self.assertEqual(response.status_code, 201)
        routes = self.app.get("/api/routes").get_json()["routes"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["name"], "Demo")
        self.assertFalse(routes[0]["isLoop"])
        deleted = self.app.delete("/api/routes/demo-route")
        self.assertEqual(deleted.status_code, 200)
        routes = self.app.get("/api/routes").get_json()["routes"]
        self.assertEqual(routes, [])

    def test_sync_last_write_wins(self):
        self.manager.create_route(self.route_payload())
        sync_payload = {
            "routes": [
                {
                    **self.route_payload(),
                    "name": "Updated on phone",
                    "updatedAt": "2026-04-02T01:00:00Z",
                }
            ]
        }
        result = self.app.post("/api/routes/sync", json=sync_payload).get_json()
        self.assertTrue(result["ok"])
        route = self.manager.get_route("demo-route")
        self.assertEqual(route["name"], "Updated on phone")

    def test_loop_flag_is_preserved_and_used_in_status(self):
        payload = {
            **self.route_payload(),
            "id": "loop-route",
            "isLoop": True,
        }
        self.manager.create_route(payload)
        activated = self.app.post("/api/routes/loop-route/activate").get_json()
        self.assertTrue(activated["isLoop"])
        stored = self.manager.get_route("loop-route")
        self.assertTrue(stored["isLoop"])

    def test_apb_sentence_contains_expected_fields(self):
        sentence = apb_sentence(123.4, -0.025, "WP1", 121.0)
        self.assertIn("$GPAPB", sentence)
        self.assertIn(",0.025,L,N,", sentence)
        self.assertIn(",123.4,T", sentence)

    def test_leg_metrics_sign_and_passed(self):
        start = (52.0, 4.0)
        end = (52.0, 4.01)
        left_of_track = (52.001, 4.005)
        xte, along, leg = leg_metrics_m(start, end, left_of_track)
        self.assertGreater(xte, 0.0)
        self.assertGreater(along, 0.0)
        self.assertGreater(leg, along)

    def test_activate_resume_and_health(self):
        self.manager.create_route(self.route_payload())
        activated = self.app.post("/api/routes/demo-route/activate").get_json()
        self.assertEqual(activated["state"], "ready")
        paused = self.app.post("/api/routes/active/stop").get_json()
        self.assertEqual(paused["state"], "paused")
        resumed = self.app.post("/api/routes/active/resume").get_json()
        self.assertEqual(resumed["state"], "ready")
        with mock.patch.object(
            self.manager,
            "_read_gpsd_summary",
            return_value={
                "ok": True,
                "connected": True,
                "deviceCount": 1,
                "devicePath": "/dev/ttyOP_gps",
                "mode": 2,
                "fixType": "2d",
                "satellitesVisible": 10,
                "satellitesUsed": 5,
                "status": "fix",
                "error": "",
            },
        ):
            health = self.app.get("/api/routes/health").get_json()
        self.assertEqual(health["routeCount"], 1)
        self.assertEqual(health["gpsSource"], "none")
        self.assertEqual(health["gpsd"]["devicePath"], "/dev/ttyOP_gps")
        self.assertEqual(health["gpsd"]["satellitesUsed"], 5)

    def test_navigation_math_helpers(self):
        distance = haversine_distance_m((52.0, 4.0), (52.0, 4.01))
        bearing = bearing_true_deg((52.0, 4.0), (52.0, 4.01))
        self.assertGreater(distance, 500.0)
        self.assertLess(distance, 900.0)
        self.assertGreater(bearing, 80.0)
        self.assertLess(bearing, 100.0)

    def test_nmea_connection_is_reused_until_failure(self):
        connection = mock.Mock()
        with mock.patch("web.route_service.manager.socket.create_connection", return_value=connection) as create_connection:
            self.manager._write_nmea("$GPAPB,1")
            self.manager._write_nmea("$GPAPB,2")
            self.assertEqual(create_connection.call_count, 1)
            self.assertEqual(connection.sendall.call_count, 2)

            connection.sendall.side_effect = OSError("boom")
            self.manager._write_nmea("$GPAPB,3")
            connection.close.assert_called_once()
            self.assertIsNone(self.manager._nmea_socket)


if __name__ == "__main__":
    unittest.main()
