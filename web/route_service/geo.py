import math

from .constants import EARTH_RADIUS_M


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def normalize_longitude_delta(delta: float) -> float:
    while delta > 180.0:
        delta -= 360.0
    while delta < -180.0:
        delta += 360.0
    return delta


def haversine_distance_m(start: tuple[float, float], end: tuple[float, float]) -> float:
    lat1, lon1 = start
    lat2, lon2 = end
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(normalize_longitude_delta(lon2 - lon1))
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def bearing_true_deg(start: tuple[float, float], end: tuple[float, float]) -> float:
    lat1, lon1 = start
    lat2, lon2 = end
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_lambda = math.radians(normalize_longitude_delta(lon2 - lon1))
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def to_local_xy_m(origin: tuple[float, float], point: tuple[float, float]) -> tuple[float, float]:
    origin_lat, origin_lon = origin
    point_lat, point_lon = point
    avg_lat = math.radians((origin_lat + point_lat) / 2.0)
    x = math.radians(normalize_longitude_delta(point_lon - origin_lon)) * EARTH_RADIUS_M * math.cos(avg_lat)
    y = math.radians(point_lat - origin_lat) * EARTH_RADIUS_M
    return x, y


def leg_metrics_m(
    start: tuple[float, float],
    end: tuple[float, float],
    position: tuple[float, float],
) -> tuple[float, float, float]:
    leg_x, leg_y = to_local_xy_m(start, end)
    pos_x, pos_y = to_local_xy_m(start, position)
    leg_length = math.hypot(leg_x, leg_y)
    if leg_length < 0.001:
        return 0.0, 0.0, 0.0
    dot = leg_x * pos_x + leg_y * pos_y
    along_track = dot / leg_length
    cross = leg_x * pos_y - leg_y * pos_x
    cross_track = cross / leg_length
    return cross_track, along_track, leg_length
