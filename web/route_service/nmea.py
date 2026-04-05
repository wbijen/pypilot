def nmea_cksum(payload: str) -> int:
    checksum = 0
    for character in payload:
        checksum ^= ord(character)
    return checksum


def apb_sentence(track_true_deg: float, xte_nm: float, waypoint_name: str, bearing_to_waypoint_deg: float) -> str:
    xte = abs(xte_nm)
    steer = "R" if xte_nm >= 0 else "L"
    fields = [
        "GPAPB",
        "A",
        "A",
        f"{xte:.3f}",
        steer,
        "N",
        "V",
        "V",
        f"{bearing_to_waypoint_deg:.1f}",
        "T",
        waypoint_name[:32] or "WP",
        f"{bearing_to_waypoint_deg:.1f}",
        "T",
        f"{track_true_deg:.1f}",
        "T",
    ]
    payload = ",".join(fields)
    return f"${payload}*{nmea_cksum(payload):02X}"
