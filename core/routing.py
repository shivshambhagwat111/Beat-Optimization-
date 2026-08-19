import logging
from typing import List, Optional, Tuple

import numpy as np
import requests

from config import OSRM_BASE_URL, OSRM_TABLE_BASE_URL

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10

# Safety cap on a single /table request: MAX_OUTLETS_PER_BEAT defaults to 35,
# comfortably under this, but the "original beats" sequencer groups outlets by
# whatever label was already in the sheet, which could be arbitrarily large.
MAX_MATRIX_POINTS = 50


def get_road_route(waypoints: List[Tuple[float, float]]) -> Optional[List[Tuple[float, float]]]:
    """Fetches the real road-following path visiting the given (lat, lon) waypoints
    in order, via an OSRM-compatible routing server. Returns a list of (lat, lon)
    points tracing the roads, or None if no route could be resolved (network error,
    no drivable path, etc.) so callers can fall back to a straight line."""
    if len(waypoints) < 2:
        return None

    coords_param = ";".join(f"{lon},{lat}" for lat, lon in waypoints)
    url = f"{OSRM_BASE_URL}/{coords_param}"

    try:
        response = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Road routing request failed: %s", exc)
        return None

    if data.get("code") != "Ok" or not data.get("routes"):
        logger.warning("Road routing returned no route (code=%s).", data.get("code"))
        return None

    coordinates = data["routes"][0]["geometry"]["coordinates"]
    return [(lat, lon) for lon, lat in coordinates]


def get_road_distance_matrix(points: List[Tuple[float, float]]) -> Optional[np.ndarray]:
    """Fetches a real road-distance matrix (km) between every pair of the given
    (lat, lon) points via an OSRM-compatible Table service. Returns an NxN numpy
    array, or None if unavailable (network error, too many points for one
    request, server doesn't support /table, etc.) so callers can fall back to
    straight-line (Haversine) distance instead."""
    n = len(points)
    if n < 2 or n > MAX_MATRIX_POINTS:
        return None

    coords_param = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"{OSRM_TABLE_BASE_URL}/{coords_param}"

    try:
        response = requests.get(
            url, params={"annotations": "distance"}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Road distance matrix request failed: %s", exc)
        return None

    if data.get("code") != "Ok" or not data.get("distances"):
        logger.warning("Road distance matrix returned no data (code=%s).", data.get("code"))
        return None

    matrix_m = np.array(data["distances"], dtype=float)
    if matrix_m.shape != (n, n):
        logger.warning(
            "Road distance matrix has unexpected shape %s for %d points.", matrix_m.shape, n
        )
        return None

    return matrix_m / 1000.0  # meters -> km, to match haversine_matrix's units
