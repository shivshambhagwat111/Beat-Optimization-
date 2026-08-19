import colorsys
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import folium
import pandas as pd

from core.optimizer import BeatOptimizer
from core.routing import get_road_route

logger = logging.getLogger(__name__)

# Bounded concurrency for per-beat/per-group road-routing calls: fast enough to
# not pay N sequential round-trips, but still capped so we don't hammer the
# shared public OSRM demo server (which is rate-limited and explicitly "not
# intended for production traffic") with an unbounded burst of requests.
MAX_CONCURRENT_ROUTING_REQUESTS = 4

# Kept short: this TSP run is only to make the "original beats" map show a
# coherent connecting line, not to find a provably optimal route, and it runs
# synchronously inside the upload request so it shouldn't be slow per beat.
ORIGINAL_ROUTE_TSP_TIME_LIMIT_SECONDS = 2

# Soft cap on total TSP solve time across all of one map's original beats,
# divided per-beat (floored at 1s) — mirrors BeatOptimizer.optimize()'s
# TOTAL_TSP_TIME_BUDGET_SECONDS, so a map with many original beats doesn't pay
# the full per-beat budget times the beat count.
TOTAL_ORIGINAL_TSP_TIME_BUDGET_SECONDS = 20

REQUIRED_COLUMNS = ["Retailer Code", "Retailer Name", "Latitude", "Longitude", "beat_id", "visit_order"]
ORIGINAL_REQUIRED_COLUMNS = ["Retailer Code", "Retailer Name", "Latitude", "Longitude", "beat_name"]
COMBINED_REQUIRED_COLUMNS = REQUIRED_COLUMNS + ["distributor_code", "distributor_name"]
COMBINED_ORIGINAL_REQUIRED_COLUMNS = ORIGINAL_REQUIRED_COLUMNS + ["distributor_code", "distributor_name"]

# A pending route to draw: (target to add the polyline to, ordered stop
# points, line color, tooltip). `target` is the map itself or a FeatureGroup
# layer (so it can be toggled on/off together with its markers).
RouteJob = Tuple[Union[folium.Map, folium.FeatureGroup], List[Tuple[float, float]], str, str]


def _generate_distinct_colors(n: int) -> List[str]:
    """Generates n visually distinct hex colors spaced evenly around the HSV hue wheel."""
    colors = []
    for i in range(n):
        hue = i / n
        r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.85)
        colors.append(f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}")
    return colors


def _new_base_map(center_lat: float, center_lon: float) -> folium.Map:
    """Creates a Folium map with switchable Street/Satellite tile layers."""
    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=12, tiles=None)

    folium.TileLayer(tiles="cartodbpositron", name="Street", control=True).add_to(fmap)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
        name="Satellite",
        control=True,
    ).add_to(fmap)
    return fmap


def _add_outlet_focus_listener(fmap: folium.Map, marker_names_by_code: Dict[str, str]) -> None:
    """Injects a postMessage listener so the parent page (the React dashboard,
    which embeds this map in an iframe) can ask the map to pan to and open the
    popup for a specific outlet, e.g. when its row is clicked in the beat list."""
    lookup_js = f"var outletMarkerNames = {json.dumps(marker_names_by_code)};"
    fmap.get_root().script.add_child(folium.Element(lookup_js))

    listener_js = f"""
    window.addEventListener('message', function(event) {{
        var data = event.data || {{}};
        if (data.type !== 'focusOutlet' || !data.retailerCode) return;
        var markerName = outletMarkerNames[data.retailerCode];
        if (!markerName || !window[markerName]) return;
        var marker = window[markerName];
        {fmap.get_name()}.setView(marker.getLatLng(), 17);
        marker.openPopup();
    }});
    """
    fmap.get_root().script.add_child(folium.Element(listener_js))


def _draw_routes_concurrently(jobs: List[RouteJob]) -> None:
    """Fetches road routes for multiple beats/groups concurrently (bounded pool,
    since `get_road_route`'s `requests.get` releases the GIL while waiting on
    the network) and draws each as a polyline once its route resolves, falling
    back to a straight line per-job if that job's request failed — same
    fallback behavior as before, just resolved concurrently instead of one
    sequential call per beat with a fixed delay between each."""
    routable = [(i, points) for i, (_, points, _, _) in enumerate(jobs) if len(points) > 1]
    if not routable:
        return

    road_routes: Dict[int, Optional[List[Tuple[float, float]]]] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_ROUTING_REQUESTS) as pool:
        future_to_index = {pool.submit(get_road_route, points): i for i, points in routable}
        for future in as_completed(future_to_index):
            road_routes[future_to_index[future]] = future.result()

    for i, (target, stop_points, color, tooltip) in enumerate(jobs):
        if len(stop_points) <= 1:
            continue
        road_route = road_routes.get(i)
        if road_route is None:
            logger.warning("%s: road routing unavailable, falling back to a straight-line path.", tooltip)
        folium.PolyLine(
            locations=road_route or stop_points, color=color, weight=3, opacity=0.8, tooltip=tooltip
        ).add_to(target)


def _sequence_original_routes_concurrently(
    sequencer: BeatOptimizer, groups: List[Tuple[Any, pd.DataFrame]]
) -> Dict[Any, pd.DataFrame]:
    """Sequences each original beat's display route concurrently (bounded pool)
    instead of one at a time. Each `sequence_route` call independently fetches
    its own road-distance matrix over the network before running a short TSP
    solve, so doing this sequentially means paying full network latency once
    per beat; running it through the same bounded thread pool used for road
    routing overlaps that latency across beats instead. Also shrinks the
    per-beat TSP time limit as the beat count grows, mirroring
    BeatOptimizer.optimize()'s total time budget, so a map with many original
    beats doesn't scale linearly with beat count. `groups` keys just need to be
    hashable (a beat_name for a single distributor, or a
    (distributor_code, beat_name) pair for a combined map)."""
    if not groups:
        return {}

    effective_time_limit = max(
        1,
        min(
            ORIGINAL_ROUTE_TSP_TIME_LIMIT_SECONDS,
            TOTAL_ORIGINAL_TSP_TIME_BUDGET_SECONDS // len(groups),
        ),
    )

    results: Dict[Any, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_ROUTING_REQUESTS) as pool:
        future_to_key = {
            pool.submit(sequencer.sequence_route, beat_df, effective_time_limit): key
            for key, beat_df in groups
        }
        for future in as_completed(future_to_key):
            results[future_to_key[future]] = future.result().sort_values("visit_order")
    return results


def generate_beat_map(
    df: pd.DataFrame, output_path: Union[str, Path] = "output/beats_map.html"
) -> Path:
    """Renders an interactive Folium map: one distinct color per beat_id, an outlet
    marker with a Retailer/Beat/Visit-Order popup, and a polyline tracing each
    beat's route in visit_order sequence. Saves to output_path and returns it."""
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if df.empty:
        raise ValueError("Cannot generate a map from an empty DataFrame.")

    center_lat = df["Latitude"].mean()
    center_lon = df["Longitude"].mean()
    fmap = _new_base_map(center_lat, center_lon)

    beat_ids = sorted(df["beat_id"].unique())
    beat_colors = dict(zip(beat_ids, _generate_distinct_colors(len(beat_ids))))
    marker_names_by_code: Dict[str, str] = {}
    route_jobs: List[RouteJob] = []

    for beat_id, beat_df in df.groupby("beat_id"):
        color = beat_colors[beat_id]
        ordered = beat_df.sort_values("visit_order")

        for _, row in ordered.iterrows():
            popup_html = (
                f"<b>Retailer Name:</b> {row['Retailer Name']}<br>"
                f"<b>Beat ID:</b> {beat_id}<br>"
                f"<b>Visit Order:</b> {int(row['visit_order'])}"
            )
            marker = folium.CircleMarker(
                location=[row["Latitude"], row["Longitude"]],
                radius=6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.9,
                popup=folium.Popup(popup_html, max_width=250),
            )
            marker.add_to(fmap)
            marker_names_by_code[row["Retailer Code"]] = marker.get_name()

        route_jobs.append(
            (fmap, list(zip(ordered["Latitude"], ordered["Longitude"])), color, f"Beat {beat_id} route")
        )

    _draw_routes_concurrently(route_jobs)
    _add_outlet_focus_listener(fmap, marker_names_by_code)
    folium.LayerControl(collapsed=False).add_to(fmap)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(output_path))

    logger.info(
        "generate_beat_map: rendered %d outlets across %d beats to %s",
        len(df), len(beat_ids), output_path,
    )
    return output_path


def generate_original_beat_map(
    df: pd.DataFrame, output_path: Union[str, Path] = "output/original_beats_map.html"
) -> Path:
    """Renders an interactive Folium map of outlets grouped by their pre-existing
    (legacy) beat_name — one distinct color per original beat, a Retailer/Beat popup
    on each marker, and a connecting polyline per beat. The original data has no
    stop-visit sequence of its own (only a beat label), so the visiting order used
    to draw each line is computed here purely for a coherent, non-crossing route —
    it is a display aid, not a record of the historical visiting order.
    Saves to output_path and returns it."""
    missing = [col for col in ORIGINAL_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if df.empty:
        raise ValueError("Cannot generate a map from an empty DataFrame.")

    center_lat = df["Latitude"].mean()
    center_lon = df["Longitude"].mean()
    fmap = _new_base_map(center_lat, center_lon)

    beat_names = sorted(df["beat_name"].unique())
    beat_colors = dict(zip(beat_names, _generate_distinct_colors(len(beat_names))))
    sequencer = BeatOptimizer(df, tsp_time_limit_seconds=ORIGINAL_ROUTE_TSP_TIME_LIMIT_SECONDS)
    marker_names_by_code: Dict[str, str] = {}
    groups: List[Tuple[str, pd.DataFrame]] = list(df.groupby("beat_name"))

    for beat_name, beat_df in groups:
        color = beat_colors[beat_name]
        for _, row in beat_df.iterrows():
            popup_html = (
                f"<b>Retailer Name:</b> {row['Retailer Name']}<br>"
                f"<b>Original Beat:</b> {beat_name}"
            )
            marker = folium.CircleMarker(
                location=[row["Latitude"], row["Longitude"]],
                radius=6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.9,
                popup=folium.Popup(popup_html, max_width=250),
            )
            marker.add_to(fmap)
            marker_names_by_code[row["Retailer Code"]] = marker.get_name()

    sequenced_by_beat = _sequence_original_routes_concurrently(sequencer, groups)
    route_jobs: List[RouteJob] = [
        (
            fmap,
            list(zip(sequenced_by_beat[beat_name]["Latitude"], sequenced_by_beat[beat_name]["Longitude"])),
            beat_colors[beat_name],
            f"Original beat: {beat_name}",
        )
        for beat_name, _ in groups
    ]

    _draw_routes_concurrently(route_jobs)
    _add_outlet_focus_listener(fmap, marker_names_by_code)
    folium.LayerControl(collapsed=False).add_to(fmap)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(output_path))

    logger.info(
        "generate_original_beat_map: rendered %d outlets across %d original beats to %s",
        len(df), len(beat_names), output_path,
    )
    return output_path


def generate_combined_beat_map(
    df: pd.DataFrame, output_path: Union[str, Path] = "output/combined_beats_map.html"
) -> Path:
    """Renders multiple distributors' optimized beats on a single map. Each
    distributor is its own toggleable layer (via the layer control), and beat
    colors are unique across the entire map — beat_id is the Beat table's
    primary key, so it's already globally unique across distributors, not just
    within one. Saves to output_path and returns it."""
    missing = [col for col in COMBINED_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if df.empty:
        raise ValueError("Cannot generate a map from an empty DataFrame.")

    center_lat = df["Latitude"].mean()
    center_lon = df["Longitude"].mean()
    fmap = _new_base_map(center_lat, center_lon)

    beat_ids = sorted(df["beat_id"].unique())
    beat_colors = dict(zip(beat_ids, _generate_distinct_colors(len(beat_ids))))
    marker_names_by_code: Dict[str, str] = {}
    route_jobs: List[RouteJob] = []

    for distributor_code, dist_df in df.groupby("distributor_code", sort=True):
        distributor_name = dist_df["distributor_name"].iloc[0]
        layer = folium.FeatureGroup(name=f"{distributor_name} ({distributor_code})", show=True)

        for beat_id, beat_df in dist_df.groupby("beat_id"):
            color = beat_colors[beat_id]
            ordered = beat_df.sort_values("visit_order")

            for _, row in ordered.iterrows():
                popup_html = (
                    f"<b>Distributor:</b> {distributor_name} ({distributor_code})<br>"
                    f"<b>Retailer Name:</b> {row['Retailer Name']}<br>"
                    f"<b>Beat ID:</b> {beat_id}<br>"
                    f"<b>Visit Order:</b> {int(row['visit_order'])}"
                )
                marker = folium.CircleMarker(
                    location=[row["Latitude"], row["Longitude"]],
                    radius=6,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.9,
                    popup=folium.Popup(popup_html, max_width=250),
                )
                marker.add_to(layer)
                marker_names_by_code[row["Retailer Code"]] = marker.get_name()

            # Deferred: added to `layer` (already registered under fmap below)
            # once _draw_routes_concurrently runs, before fmap.save() renders it.
            route_jobs.append(
                (layer, list(zip(ordered["Latitude"], ordered["Longitude"])), color, f"{distributor_code} Beat {beat_id}")
            )

        layer.add_to(fmap)

    _draw_routes_concurrently(route_jobs)
    _add_outlet_focus_listener(fmap, marker_names_by_code)
    folium.LayerControl(collapsed=False).add_to(fmap)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(output_path))

    logger.info(
        "generate_combined_beat_map: rendered %d outlets across %d beats for %d distributors to %s",
        len(df), len(beat_ids), df["distributor_code"].nunique(), output_path,
    )
    return output_path


def generate_combined_original_beat_map(
    df: pd.DataFrame, output_path: Union[str, Path] = "output/combined_original_beats_map.html"
) -> Path:
    """Renders multiple distributors' original (pre-existing) beat groupings on a
    single map, each distributor as its own toggleable layer. Unlike beat_id in
    the optimized map, beat_name is arbitrary sheet text and can collide across
    distributors (e.g. two distributors both using "North Zone"), so colors and
    route-sequencing groups are keyed on (distributor_code, beat_name) pairs, not
    beat_name alone. Saves to output_path and returns it."""
    missing = [col for col in COMBINED_ORIGINAL_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if df.empty:
        raise ValueError("Cannot generate a map from an empty DataFrame.")

    center_lat = df["Latitude"].mean()
    center_lon = df["Longitude"].mean()
    fmap = _new_base_map(center_lat, center_lon)

    group_keys = sorted(
        df[["distributor_code", "beat_name"]].drop_duplicates().itertuples(index=False, name=None)
    )
    group_colors = dict(zip(group_keys, _generate_distinct_colors(len(group_keys))))
    marker_names_by_code: Dict[str, str] = {}
    layers: Dict[str, folium.FeatureGroup] = {}
    beat_groups: List[Tuple[Tuple[str, str], pd.DataFrame]] = []

    for distributor_code, dist_df in df.groupby("distributor_code", sort=True):
        distributor_name = dist_df["distributor_name"].iloc[0]
        layer = folium.FeatureGroup(name=f"{distributor_name} ({distributor_code})", show=True)
        layers[distributor_code] = layer

        for beat_name, beat_df in dist_df.groupby("beat_name"):
            color = group_colors[(distributor_code, beat_name)]

            for _, row in beat_df.iterrows():
                popup_html = (
                    f"<b>Distributor:</b> {distributor_name} ({distributor_code})<br>"
                    f"<b>Retailer Name:</b> {row['Retailer Name']}<br>"
                    f"<b>Original Beat:</b> {beat_name}"
                )
                marker = folium.CircleMarker(
                    location=[row["Latitude"], row["Longitude"]],
                    radius=6,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.9,
                    popup=folium.Popup(popup_html, max_width=250),
                )
                marker.add_to(layer)
                marker_names_by_code[row["Retailer Code"]] = marker.get_name()

            beat_groups.append(((distributor_code, beat_name), beat_df))

        layer.add_to(fmap)

    # One sequencer shared across every distributor: sequence_route() doesn't
    # read back from `self.df`, only `self.tsp_time_limit_seconds` (same for
    # all distributors here), so this lets every distributor's beats be
    # sequenced through a single flat, bounded thread pool instead of one pool
    # per distributor run one after another.
    sequencer = BeatOptimizer(df, tsp_time_limit_seconds=ORIGINAL_ROUTE_TSP_TIME_LIMIT_SECONDS)
    sequenced_by_key = _sequence_original_routes_concurrently(sequencer, beat_groups)

    route_jobs: List[RouteJob] = [
        (
            layers[distributor_code],
            list(zip(sequenced["Latitude"], sequenced["Longitude"])),
            group_colors[(distributor_code, beat_name)],
            f"{distributor_code}: {beat_name}",
        )
        for (distributor_code, beat_name), sequenced in sequenced_by_key.items()
    ]

    _draw_routes_concurrently(route_jobs)
    _add_outlet_focus_listener(fmap, marker_names_by_code)
    folium.LayerControl(collapsed=False).add_to(fmap)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmap.save(str(output_path))

    logger.info(
        "generate_combined_original_beat_map: rendered %d outlets across %d original beats "
        "for %d distributors to %s",
        len(df), len(group_keys), df["distributor_code"].nunique(), output_path,
    )
    return output_path
