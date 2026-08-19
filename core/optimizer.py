import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional

import numpy as np
import pandas as pd
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from ortools.graph.python import min_cost_flow
from scipy.cluster.vq import kmeans2

from config import MAX_OUTLETS_PER_BEAT, MIN_OUTLETS_PER_BEAT
from core.routing import get_road_distance_matrix

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088
COST_SCALE = 1000  # km -> integer units for OR-Tools (integer-cost solvers)

# Soft cap on total TSP solve time across all beats in one optimize() run,
# divided per-beat (floored at 1s) so large runs don't pay the full per-beat
# budget times the beat count.
TOTAL_TSP_TIME_BUDGET_SECONDS = 20

# Bounded concurrency for per-beat road-distance-matrix fetches, matching the
# same rationale as core/visualizer.py's MAX_CONCURRENT_ROUTING_REQUESTS.
MAX_CONCURRENT_MATRIX_REQUESTS = 4


def haversine_matrix(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Pairwise great-circle distance (km) between two sets of lat/lon points."""
    lat1 = np.radians(np.asarray(lat1, dtype=float))[:, None]
    lon1 = np.radians(np.asarray(lon1, dtype=float))[:, None]
    lat2 = np.radians(np.asarray(lat2, dtype=float))[None, :]
    lon2 = np.radians(np.asarray(lon2, dtype=float))[None, :]

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    c = 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return EARTH_RADIUS_KM * c


class BeatOptimizer:
    """Groups active outlets into capacity-bounded beats and sequences each beat's route."""

    def __init__(
        self,
        df: pd.DataFrame,
        min_outlets: int = MIN_OUTLETS_PER_BEAT,
        max_outlets: int = MAX_OUTLETS_PER_BEAT,
        max_iterations: int = 15,
        tsp_time_limit_seconds: int = 3,
        random_state: int = 42,
    ):
        if min_outlets <= 0 or max_outlets < min_outlets:
            raise ValueError("Invalid min_outlets/max_outlets bounds.")

        self.df = df.reset_index(drop=True).copy()
        self.min_outlets = min_outlets
        self.max_outlets = max_outlets
        self.max_iterations = max_iterations
        self.tsp_time_limit_seconds = tsp_time_limit_seconds
        self.random_state = random_state

    # ------------------------------------------------------------------
    # Task 1: capacitated clustering
    # ------------------------------------------------------------------
    def cluster_beats(self) -> pd.DataFrame:
        """Assigns each outlet to a beat_id such that every beat has strictly between
        min_outlets and max_outlets outlets, minimizing total spatial distance to the
        beat's centroid via an iterative capacitated k-means heuristic."""
        df = self.df.copy()
        n = len(df)

        if n < self.min_outlets:
            raise ValueError(
                f"Only {n} outlets available; need at least {self.min_outlets} "
                f"to form a single beat."
            )

        k = self._determine_beat_count(n)
        beat_sizes = self._compute_beat_sizes(n, k)

        lat = df["Latitude"].to_numpy(dtype=float)
        lon = df["Longitude"].to_numpy(dtype=float)
        coords = np.column_stack([lat, lon])

        centroids = self._initial_centroids(coords, k)
        assignment: Optional[np.ndarray] = None

        for iteration in range(1, self.max_iterations + 1):
            distances = haversine_matrix(lat, lon, centroids[:, 0], centroids[:, 1])
            new_assignment = self._solve_capacitated_assignment(distances, beat_sizes)
            centroids = self._recompute_centroids(coords, new_assignment, k)

            converged = assignment is not None and np.array_equal(assignment, new_assignment)
            assignment = new_assignment
            if converged:
                logger.info(
                    "BeatOptimizer: cluster assignment converged after %d iteration(s).",
                    iteration,
                )
                break
        else:
            logger.info(
                "BeatOptimizer: reached max_iterations=%d without full convergence.",
                self.max_iterations,
            )

        df["beat_id"] = assignment + 1  # 1-indexed beat ids
        sizes = df["beat_id"].value_counts().sort_index()
        logger.info(
            "BeatOptimizer: clustered %d outlets into %d beats (sizes: %s).",
            n, k, sizes.to_dict(),
        )
        return df

    def _determine_beat_count(self, n: int) -> int:
        k_min = math.ceil(n / self.max_outlets)
        k_max = n // self.min_outlets
        if k_min > k_max:
            raise ValueError(
                f"Cannot partition {n} outlets into beats of between "
                f"{self.min_outlets} and {self.max_outlets} outlets each."
            )
        midpoint = (self.min_outlets + self.max_outlets) / 2
        ideal = round(n / midpoint) if midpoint else k_min
        return int(min(max(ideal, k_min), k_max))

    def _compute_beat_sizes(self, n: int, k: int) -> np.ndarray:
        base, remainder = divmod(n, k)
        sizes = np.full(k, base, dtype=int)
        sizes[:remainder] += 1  # distribute the remainder, each size stays in [min, max]
        return sizes

    def _initial_centroids(self, coords: np.ndarray, k: int) -> np.ndarray:
        if k == 1:
            return coords.mean(axis=0, keepdims=True)
        centroids, _ = kmeans2(coords, k, minit="++", seed=self.random_state)
        return centroids

    def _solve_capacitated_assignment(
        self, distances: np.ndarray, beat_sizes: np.ndarray
    ) -> np.ndarray:
        """Optimal outlet -> beat assignment respecting exact beat_sizes, via min-cost flow."""
        n, k = distances.shape
        smcf = min_cost_flow.SimpleMinCostFlow()

        for outlet in range(n):
            row_cost = (distances[outlet] * COST_SCALE).astype(np.int64)
            for beat in range(k):
                smcf.add_arc_with_capacity_and_unit_cost(
                    outlet, n + beat, 1, int(row_cost[beat])
                )

        for outlet in range(n):
            smcf.set_node_supply(outlet, 1)
        for beat in range(k):
            smcf.set_node_supply(n + beat, -int(beat_sizes[beat]))

        status = smcf.solve()
        if status != smcf.OPTIMAL:
            raise RuntimeError(f"Capacitated clustering assignment failed with status {status}.")

        assignment = np.full(n, -1, dtype=int)
        for arc in range(smcf.num_arcs()):
            if smcf.flow(arc) > 0:
                assignment[smcf.tail(arc)] = smcf.head(arc) - n
        return assignment

    def _recompute_centroids(self, coords: np.ndarray, assignment: np.ndarray, k: int) -> np.ndarray:
        centroids = np.zeros((k, 2))
        for beat in range(k):
            members = coords[assignment == beat]
            centroids[beat] = members.mean(axis=0)
        return centroids

    # ------------------------------------------------------------------
    # Task 2: route sequencing (open-path TSP via Haversine distance)
    # ------------------------------------------------------------------
    def sequence_route(
        self,
        beat_df: pd.DataFrame,
        time_limit_seconds: Optional[int] = None,
        distance_matrix_km: Optional[np.ndarray] = None,
    ) -> pd.DataFrame:
        """Solves an open-path TSP (no return to start) over one beat's outlets and
        returns a copy of beat_df with a 1..N visit_order column. `time_limit_seconds`
        overrides self.tsp_time_limit_seconds for this call only, letting `optimize()`
        shrink the per-beat budget on runs with many beats.

        `distance_matrix_km`, if given, is used as the TSP cost matrix directly —
        this is how `optimize()` passes in a real-road distance matrix so the
        solver picks an order that makes sense on actual roads (e.g. around a
        river) rather than one that only looks short as the crow flies. If not
        given, a real-road matrix is fetched here instead, falling back to
        straight-line (Haversine) distance if that's unavailable."""
        beat_df = beat_df.reset_index(drop=True).copy()
        n = len(beat_df)

        if n <= 2:
            beat_df["visit_order"] = np.arange(1, n + 1)
            return beat_df

        lat = beat_df["Latitude"].to_numpy(dtype=float)
        lon = beat_df["Longitude"].to_numpy(dtype=float)

        if distance_matrix_km is not None:
            dist = distance_matrix_km
        else:
            dist = get_road_distance_matrix(list(zip(lat, lon)))
            if dist is None:
                dist = haversine_matrix(lat, lon, lat, lon)

        # Augment with a dummy depot node (index n) at zero cost to/from every real
        # node. Solving a closed tour on this graph and then removing the depot
        # yields the optimal open Hamiltonian path (free start and end) over the
        # real outlets, since the depot's zero-cost edges never add distance.
        size = n + 1
        dist_matrix = np.zeros((size, size))
        dist_matrix[:n, :n] = dist
        int_matrix = (dist_matrix * COST_SCALE).astype(np.int64)

        manager = pywrapcp.RoutingIndexManager(size, 1, n)
        routing = pywrapcp.RoutingModel(manager)

        def distance_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return int(int_matrix[from_node][to_node])

        transit_callback_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        )
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_parameters.time_limit.FromSeconds(
            time_limit_seconds if time_limit_seconds is not None else self.tsp_time_limit_seconds
        )

        solution = routing.SolveWithParameters(search_parameters)
        if solution is None:
            raise RuntimeError("No TSP solution found for this beat.")

        visit_order = np.empty(n, dtype=int)
        sequence = 1
        index = routing.Start(0)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node != n:  # skip the dummy depot
                visit_order[node] = sequence
                sequence += 1
            index = solution.Value(routing.NextVar(index))

        beat_df["visit_order"] = visit_order
        return beat_df

    # ------------------------------------------------------------------
    # Convenience: run both stages end-to-end
    # ------------------------------------------------------------------
    def optimize(self) -> pd.DataFrame:
        clustered = self.cluster_beats()

        # Cap the *total* TSP solve time rather than letting it grow linearly
        # with beat count: small runs keep the full per-beat budget, large runs
        # shrink toward a 1-second-per-beat floor.
        n_beats = clustered["beat_id"].nunique()
        effective_tsp_limit = max(
            1, min(self.tsp_time_limit_seconds, TOTAL_TSP_TIME_BUDGET_SECONDS // n_beats)
        )

        beat_groups = list(clustered.groupby("beat_id", sort=True))

        # Fetch every beat's real-road distance matrix concurrently (bounded
        # pool), falling back to Haversine per-beat wherever that fails, then
        # pass each resolved matrix into sequence_route explicitly — this keeps
        # the network dependency from re-introducing the sequential per-beat
        # latency the earlier performance pass removed.
        road_matrices: Dict[int, Optional[np.ndarray]] = {}
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_MATRIX_REQUESTS) as pool:
            future_to_beat = {
                pool.submit(
                    get_road_distance_matrix,
                    list(zip(group["Latitude"], group["Longitude"])),
                ): beat_id
                for beat_id, group in beat_groups
            }
            for future in as_completed(future_to_beat):
                road_matrices[future_to_beat[future]] = future.result()

        sequenced = []
        for beat_id, group in beat_groups:
            matrix = road_matrices.get(beat_id)
            if matrix is None:
                lat = group["Latitude"].to_numpy(dtype=float)
                lon = group["Longitude"].to_numpy(dtype=float)
                matrix = haversine_matrix(lat, lon, lat, lon)
            sequenced.append(
                self.sequence_route(
                    group, time_limit_seconds=effective_tsp_limit, distance_matrix_km=matrix
                )
            )
        result = pd.concat(sequenced, ignore_index=True)
        logger.info(
            "BeatOptimizer: optimization complete for %d outlets across %d beats "
            "(per-beat TSP time limit: %ds).",
            len(result), n_beats, effective_tsp_limit,
        )
        return result
