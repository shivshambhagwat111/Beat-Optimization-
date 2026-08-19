import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

import pandas as pd
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import insert, update
from sqlalchemy.orm import Session

from api.db import SessionLocal, get_db, init_db
from api.models import Beat, Distributor, Outlet, RouteSequence
from config import FRONTEND_ORIGINS
from core.data_loader import RetailDataLoader
from core.optimizer import BeatOptimizer
from core.visualizer import (
    generate_beat_map,
    generate_combined_beat_map,
    generate_combined_original_beat_map,
    generate_original_beat_map,
)

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class UploadResponse(BaseModel):
    message: str
    distributor_codes: List[str]
    total_outlets: int
    inserted: int
    updated: int
    duplicates_removed: int


class OptimizeResponse(BaseModel):
    message: str
    active_outlets: int


class DistributorOut(BaseModel):
    code: str
    name: str


class DistributorNameUpdate(BaseModel):
    name: str


class OutletOut(BaseModel):
    retailer_code: str
    name: str
    latitude: float
    longitude: float
    visit_order: int
    # Only populated on the jointly-optimized combined response, to show each
    # outlet's originating distributor once it's merged into a shared beat.
    distributor_code: str | None = None
    distributor_name: str | None = None


class BeatOut(BaseModel):
    beat_id: int
    beat_code: str
    total_outlets: int
    outlets: List[OutletOut]


class BeatsResponse(BaseModel):
    distributor_code: str
    distributor_name: str
    beats: List[BeatOut]


class OriginalOutletOut(BaseModel):
    retailer_code: str
    name: str
    latitude: float
    longitude: float


class OriginalBeatOut(BaseModel):
    beat_name: str
    total_outlets: int
    outlets: List[OriginalOutletOut]


class OriginalBeatsResponse(BaseModel):
    distributor_code: str
    distributor_name: str
    beats: List[OriginalBeatOut]


class CombinedDistributorBeats(BaseModel):
    distributor_code: str
    distributor_name: str
    beats: List[BeatOut]


class CombinedBeatsResponse(BaseModel):
    distributor_codes: List[str]
    distributors: List[CombinedDistributorBeats]


class CombinedDistributorOriginalBeats(BaseModel):
    distributor_code: str
    distributor_name: str
    beats: List[OriginalBeatOut]


class CombinedOriginalBeatsResponse(BaseModel):
    distributor_codes: List[str]
    distributors: List[CombinedDistributorOriginalBeats]


class CombinedOptimizedBeatsResponse(BaseModel):
    distributor_codes: List[str]
    beats: List[BeatOut]


# ---------------------------------------------------------------------------
# Background task: cluster + sequence + persist + render map
# ---------------------------------------------------------------------------
def run_beat_optimization(distributor_code: str) -> None:
    db = SessionLocal()
    try:
        outlets = (
            db.query(Outlet)
            .filter(Outlet.distributor_code == distributor_code, Outlet.is_active.is_(True))
            .all()
        )
        if not outlets:
            logger.warning(
                "Optimization skipped: no active outlets for distributor '%s'.", distributor_code
            )
            return

        outlets_df = pd.DataFrame(
            [
                {
                    "outlet_id": outlet.id,
                    "Retailer Code": outlet.retailer_code,
                    "Retailer Name": outlet.name,
                    "Latitude": outlet.latitude,
                    "Longitude": outlet.longitude,
                }
                for outlet in outlets
            ]
        )

        optimizer = BeatOptimizer(outlets_df)
        result_df = optimizer.optimize()

        # Replace any beats/route sequences from a previous optimization run.
        old_beats = db.query(Beat).filter(Beat.distributor_code == distributor_code).all()
        for old_beat in old_beats:
            db.query(RouteSequence).filter(RouteSequence.beat_id == old_beat.id).delete()
            db.delete(old_beat)
        db.flush()

        for beat_number, beat_group in result_df.groupby("beat_id"):
            beat = Beat(
                beat_code=f"{distributor_code}-B{int(beat_number):03d}",
                distributor_code=distributor_code,
                total_outlets=len(beat_group),
            )
            db.add(beat)
            db.flush()  # populate beat.id for the route sequence rows below

            for _, row in beat_group.sort_values("visit_order").iterrows():
                db.add(
                    RouteSequence(
                        beat_id=beat.id,
                        outlet_id=int(row["outlet_id"]),
                        visit_order=int(row["visit_order"]),
                    )
                )

        # Render the map BEFORE committing: the frontend polls GET /api/beats and
        # treats a successful response as "everything is ready," so the map file
        # must already be fully written by the time that commit becomes visible —
        # otherwise a click on an outlet can hit a stale or not-yet-written map.
        generate_beat_map(result_df, output_path=f"output/beats_map_{distributor_code}.html")
        db.commit()
        logger.info("Beat optimization completed for distributor '%s'.", distributor_code)
    except Exception:
        db.rollback()
        logger.exception("Beat optimization failed for distributor '%s'.", distributor_code)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Background task: joint (cross-distributor) cluster + sequence + persist + map
# ---------------------------------------------------------------------------
def run_combined_beat_optimization(distributor_codes: List[str], combined_key: str) -> None:
    db = SessionLocal()
    try:
        outlets = (
            db.query(Outlet)
            .filter(Outlet.distributor_code.in_(distributor_codes), Outlet.is_active.is_(True))
            .all()
        )
        if not outlets:
            logger.warning(
                "Combined optimization skipped: no active outlets for distributors '%s'.",
                combined_key,
            )
            return

        outlets_df = pd.DataFrame(
            [
                {
                    "outlet_id": outlet.id,
                    "Retailer Code": outlet.retailer_code,
                    "Retailer Name": outlet.name,
                    "Latitude": outlet.latitude,
                    "Longitude": outlet.longitude,
                }
                for outlet in outlets
            ]
        )

        optimizer = BeatOptimizer(outlets_df)
        result_df = optimizer.optimize()

        # Replace beats from a previous joint run over this exact distributor set.
        # Filtering by combined_key (never distributor_code) keeps this fully
        # isolated from per-distributor beats, which always have combined_key null.
        old_beats = db.query(Beat).filter(Beat.combined_key == combined_key).all()
        for old_beat in old_beats:
            db.query(RouteSequence).filter(RouteSequence.beat_id == old_beat.id).delete()
            db.delete(old_beat)
        db.flush()

        for beat_number, beat_group in result_df.groupby("beat_id"):
            beat = Beat(
                beat_code=f"{combined_key}-B{int(beat_number):03d}",
                distributor_code=None,
                combined_key=combined_key,
                total_outlets=len(beat_group),
            )
            db.add(beat)
            db.flush()

            for _, row in beat_group.sort_values("visit_order").iterrows():
                db.add(
                    RouteSequence(
                        beat_id=beat.id,
                        outlet_id=int(row["outlet_id"]),
                        visit_order=int(row["visit_order"]),
                    )
                )

        generate_beat_map(
            result_df,
            output_path=f"output/{_combined_map_filename(distributor_codes, 'combined_optimized_beats_map')}",
        )
        db.commit()
        logger.info("Combined beat optimization completed for distributors '%s'.", combined_key)
    except Exception:
        db.rollback()
        logger.exception("Combined beat optimization failed for distributors '%s'.", combined_key)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api", tags=["beats"])


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def upload_outlets(file: UploadFile = File(...), db: Session = Depends(get_db)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name
        df = RetailDataLoader(tmp_path).load()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        logger.exception("Failed to parse uploaded file '%s'.", file.filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to parse the uploaded file.",
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active outlets with valid coordinates were found in the uploaded file.",
        )

    # Real-world master sheets can repeat the same outlet across multiple rows
    # (re-entry, multi-SKU listings, etc). retailer_code is the DB identity for an
    # outlet, so keep only the last occurrence per code before upserting.
    before_dedup = len(df)
    df = df.drop_duplicates(subset="Retailer Code", keep="last")
    duplicates_removed = before_dedup - len(df)
    if duplicates_removed:
        logger.warning(
            "Upload '%s': dropped %d duplicate Retailer Code row(s), keeping the last occurrence.",
            file.filename, duplicates_removed,
        )

    try:
        distributor_codes = sorted(df["Distributor Code"].unique().tolist())
        for code in distributor_codes:
            if not db.query(Distributor).filter(Distributor.code == code).first():
                db.add(Distributor(code=code, name=code))
        db.flush()

        # One round-trip to find which uploaded retailer codes already exist,
        # instead of a SELECT per row — the dominant cost for large sheets.
        existing_ids_by_code = {
            code: outlet_id
            for outlet_id, code in db.query(Outlet.id, Outlet.retailer_code)
            .filter(Outlet.retailer_code.in_(df["Retailer Code"].tolist()))
            .all()
        }

        insert_rows = []
        update_rows = []
        for _, row in df.iterrows():
            payload = {
                "distributor_code": row["Distributor Code"],
                "retailer_code": row["Retailer Code"],
                "name": row["Retailer Name"],
                "latitude": float(row["Latitude"]),
                "longitude": float(row["Longitude"]),
                "is_active": True,
                "original_beat_name": row.get("Sales Route") or None,
            }
            existing_id = existing_ids_by_code.get(row["Retailer Code"])
            if existing_id is not None:
                payload["id"] = existing_id
                update_rows.append(payload)
            else:
                insert_rows.append(payload)

        # ORM-enabled bulk insert/update: one batched (executemany) statement
        # each instead of one round-trip per row.
        if insert_rows:
            db.execute(insert(Outlet), insert_rows)
        if update_rows:
            db.execute(update(Outlet), update_rows)
        db.commit()

        inserted = len(insert_rows)
        updated = len(update_rows)

        # The original beat grouping needs no computation (it's already in the
        # sheet), so render it right away rather than waiting for an optimize run.
        for code in distributor_codes:
            outlets_with_original_beat = (
                db.query(Outlet)
                .filter(
                    Outlet.distributor_code == code,
                    Outlet.is_active.is_(True),
                    Outlet.original_beat_name.isnot(None),
                )
                .all()
            )
            if outlets_with_original_beat:
                original_df = pd.DataFrame(
                    [
                        {
                            "Retailer Code": o.retailer_code,
                            "Retailer Name": o.name,
                            "Latitude": o.latitude,
                            "Longitude": o.longitude,
                            "beat_name": o.original_beat_name,
                        }
                        for o in outlets_with_original_beat
                    ]
                )
                generate_original_beat_map(
                    original_df, output_path=f"output/original_beats_map_{code}.html"
                )

        response = UploadResponse(
            message="Outlets uploaded successfully.",
            distributor_codes=distributor_codes,
            total_outlets=len(df),
            inserted=inserted,
            updated=updated,
            duplicates_removed=duplicates_removed,
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to save outlets from '%s' to the database.", file.filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save outlets to the database.",
        )

    return response


@router.get("/distributors/{distributor_code}", response_model=DistributorOut)
def get_distributor(distributor_code: str, db: Session = Depends(get_db)):
    distributor = db.query(Distributor).filter(Distributor.code == distributor_code).first()
    if distributor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Distributor '{distributor_code}' not found.",
        )
    return DistributorOut(code=distributor.code, name=distributor.name)


@router.put("/distributors/{distributor_code}", response_model=DistributorOut)
def update_distributor_name(
    distributor_code: str, payload: DistributorNameUpdate, db: Session = Depends(get_db)
):
    distributor = db.query(Distributor).filter(Distributor.code == distributor_code).first()
    if distributor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Distributor '{distributor_code}' not found.",
        )

    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Distributor name cannot be empty."
        )

    distributor.name = name
    db.commit()
    return DistributorOut(code=distributor.code, name=distributor.name)


@router.post(
    "/optimize/combined",
    response_model=OptimizeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def optimize_combined_beats(
    codes: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    # Declared before /optimize/{distributor_code} below: Starlette matches routes
    # in registration order, and "combined" would otherwise be swallowed as a
    # {distributor_code} path value by that route.
    distributor_codes = _parse_combined_codes(codes)

    for code in distributor_codes:
        if not db.query(Distributor).filter(Distributor.code == code).first():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Distributor '{code}' not found."
            )

    active_count = (
        db.query(Outlet)
        .filter(Outlet.distributor_code.in_(distributor_codes), Outlet.is_active.is_(True))
        .count()
    )
    if active_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No active outlets found for distributors '{', '.join(distributor_codes)}'.",
        )

    combined_key = "_".join(distributor_codes)
    background_tasks.add_task(run_combined_beat_optimization, distributor_codes, combined_key)
    return OptimizeResponse(
        message=f"Combined beat optimization started for distributors '{', '.join(distributor_codes)}'.",
        active_outlets=active_count,
    )


@router.post(
    "/optimize/{distributor_code}",
    response_model=OptimizeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def optimize_beats(
    distributor_code: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    distributor = db.query(Distributor).filter(Distributor.code == distributor_code).first()
    if distributor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Distributor '{distributor_code}' not found.",
        )

    active_count = (
        db.query(Outlet)
        .filter(Outlet.distributor_code == distributor_code, Outlet.is_active.is_(True))
        .count()
    )
    if active_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No active outlets found for distributor '{distributor_code}'.",
        )

    background_tasks.add_task(run_beat_optimization, distributor_code)
    return OptimizeResponse(
        message=f"Beat optimization started for distributor '{distributor_code}'.",
        active_outlets=active_count,
    )


def _parse_combined_codes(codes: str) -> List[str]:
    parsed = sorted({c.strip() for c in codes.split(",") if c.strip()})
    if len(parsed) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least 2 distributor codes (comma-separated) for a combined view.",
        )
    return parsed


def _combined_map_filename(codes: List[str], prefix: str) -> str:
    return f"{prefix}_{'_'.join(codes)}.html"


@router.get("/beats/combined", response_model=CombinedBeatsResponse)
def get_combined_beats(codes: str, db: Session = Depends(get_db)):
    distributor_codes = _parse_combined_codes(codes)

    distributors_payload: List[CombinedDistributorBeats] = []
    combined_rows = []

    for code in distributor_codes:
        distributor = db.query(Distributor).filter(Distributor.code == code).first()
        if distributor is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Distributor '{code}' not found."
            )

        beats = (
            db.query(Beat).filter(Beat.distributor_code == code).order_by(Beat.beat_code).all()
        )
        if not beats:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No beats found for distributor '{code}'. "
                    f"Run POST /api/optimize/{code} first."
                ),
            )

        beat_payload = []
        for beat in beats:
            outlets_payload = []
            for route in beat.route_sequences:  # ordered by visit_order (see models.py)
                outlets_payload.append(
                    OutletOut(
                        retailer_code=route.outlet.retailer_code,
                        name=route.outlet.name,
                        latitude=route.outlet.latitude,
                        longitude=route.outlet.longitude,
                        visit_order=route.visit_order,
                    )
                )
                combined_rows.append(
                    {
                        "Retailer Code": route.outlet.retailer_code,
                        "Retailer Name": route.outlet.name,
                        "Latitude": route.outlet.latitude,
                        "Longitude": route.outlet.longitude,
                        "distributor_code": code,
                        "distributor_name": distributor.name,
                        "beat_id": beat.id,
                        "visit_order": route.visit_order,
                    }
                )
            beat_payload.append(
                BeatOut(
                    beat_id=beat.id,
                    beat_code=beat.beat_code,
                    total_outlets=beat.total_outlets,
                    outlets=outlets_payload,
                )
            )

        distributors_payload.append(
            CombinedDistributorBeats(
                distributor_code=code, distributor_name=distributor.name, beats=beat_payload
            )
        )

    combined_df = pd.DataFrame(combined_rows)
    filename = _combined_map_filename(distributor_codes, "combined_beats_map")
    generate_combined_beat_map(combined_df, output_path=f"output/{filename}")

    return CombinedBeatsResponse(
        distributor_codes=distributor_codes, distributors=distributors_payload
    )


@router.get("/original-beats/combined", response_model=CombinedOriginalBeatsResponse)
def get_combined_original_beats(codes: str, db: Session = Depends(get_db)):
    distributor_codes = _parse_combined_codes(codes)

    distributors_payload: List[CombinedDistributorOriginalBeats] = []
    combined_rows = []

    for code in distributor_codes:
        distributor = db.query(Distributor).filter(Distributor.code == code).first()
        if distributor is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Distributor '{code}' not found."
            )

        outlets = (
            db.query(Outlet)
            .filter(
                Outlet.distributor_code == code,
                Outlet.is_active.is_(True),
                Outlet.original_beat_name.isnot(None),
            )
            .order_by(Outlet.original_beat_name, Outlet.name)
            .all()
        )
        if not outlets:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No original beat data found for distributor '{code}'. "
                    f"The uploaded file needs a 'Sales Route' column for this view."
                ),
            )

        beats_by_name: dict[str, list[Outlet]] = {}
        for outlet in outlets:
            beats_by_name.setdefault(outlet.original_beat_name, []).append(outlet)
            combined_rows.append(
                {
                    "Retailer Code": outlet.retailer_code,
                    "Retailer Name": outlet.name,
                    "Latitude": outlet.latitude,
                    "Longitude": outlet.longitude,
                    "distributor_code": code,
                    "distributor_name": distributor.name,
                    "beat_name": outlet.original_beat_name,
                }
            )

        beat_payload = [
            OriginalBeatOut(
                beat_name=beat_name,
                total_outlets=len(group),
                outlets=[
                    OriginalOutletOut(
                        retailer_code=o.retailer_code,
                        name=o.name,
                        latitude=o.latitude,
                        longitude=o.longitude,
                    )
                    for o in group
                ],
            )
            for beat_name, group in sorted(beats_by_name.items())
        ]

        distributors_payload.append(
            CombinedDistributorOriginalBeats(
                distributor_code=code, distributor_name=distributor.name, beats=beat_payload
            )
        )

    combined_df = pd.DataFrame(combined_rows)
    filename = _combined_map_filename(distributor_codes, "combined_original_beats_map")
    generate_combined_original_beat_map(combined_df, output_path=f"output/{filename}")

    return CombinedOriginalBeatsResponse(
        distributor_codes=distributor_codes, distributors=distributors_payload
    )


@router.get("/beats/combined-optimized", response_model=CombinedOptimizedBeatsResponse)
def get_combined_optimized_beats(codes: str, db: Session = Depends(get_db)):
    # Declared before /beats/{distributor_code} below for the same routing-order
    # reason as /optimize/combined above.
    distributor_codes = _parse_combined_codes(codes)

    for code in distributor_codes:
        if not db.query(Distributor).filter(Distributor.code == code).first():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Distributor '{code}' not found."
            )

    combined_key = "_".join(distributor_codes)
    beats = (
        db.query(Beat)
        .filter(Beat.combined_key == combined_key)
        .order_by(Beat.beat_code)
        .all()
    )
    if not beats:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No jointly-optimized beats found for distributors "
                f"'{', '.join(distributor_codes)}'. Run POST /api/optimize/combined?codes=... first."
            ),
        )

    distributor_names = {
        d.code: d.name
        for d in db.query(Distributor).filter(Distributor.code.in_(distributor_codes)).all()
    }

    beat_payload = [
        BeatOut(
            beat_id=beat.id,
            beat_code=beat.beat_code,
            total_outlets=beat.total_outlets,
            outlets=[
                OutletOut(
                    retailer_code=route.outlet.retailer_code,
                    name=route.outlet.name,
                    latitude=route.outlet.latitude,
                    longitude=route.outlet.longitude,
                    visit_order=route.visit_order,
                    distributor_code=route.outlet.distributor_code,
                    distributor_name=distributor_names.get(route.outlet.distributor_code),
                )
                for route in beat.route_sequences
            ],
        )
        for beat in beats
    ]

    return CombinedOptimizedBeatsResponse(distributor_codes=distributor_codes, beats=beat_payload)


@router.get("/beats/{distributor_code}", response_model=BeatsResponse)
def get_beats(distributor_code: str, db: Session = Depends(get_db)):
    distributor = db.query(Distributor).filter(Distributor.code == distributor_code).first()
    if distributor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Distributor '{distributor_code}' not found.",
        )

    beats = (
        db.query(Beat)
        .filter(Beat.distributor_code == distributor_code)
        .order_by(Beat.beat_code)
        .all()
    )
    if not beats:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No beats found for distributor '{distributor_code}'. "
                f"Run POST /api/optimize/{distributor_code} first."
            ),
        )

    beat_payload = [
        BeatOut(
            beat_id=beat.id,
            beat_code=beat.beat_code,
            total_outlets=beat.total_outlets,
            outlets=[
                OutletOut(
                    retailer_code=route.outlet.retailer_code,
                    name=route.outlet.name,
                    latitude=route.outlet.latitude,
                    longitude=route.outlet.longitude,
                    visit_order=route.visit_order,
                )
                for route in beat.route_sequences  # ordered by visit_order (see models.py)
            ],
        )
        for beat in beats
    ]

    return BeatsResponse(
        distributor_code=distributor_code,
        distributor_name=distributor.name,
        beats=beat_payload,
    )


@router.get("/original-beats/{distributor_code}", response_model=OriginalBeatsResponse)
def get_original_beats(distributor_code: str, db: Session = Depends(get_db)):
    distributor = db.query(Distributor).filter(Distributor.code == distributor_code).first()
    if distributor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Distributor '{distributor_code}' not found.",
        )

    outlets = (
        db.query(Outlet)
        .filter(
            Outlet.distributor_code == distributor_code,
            Outlet.is_active.is_(True),
            Outlet.original_beat_name.isnot(None),
        )
        .order_by(Outlet.original_beat_name, Outlet.name)
        .all()
    )
    if not outlets:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No original beat data found for distributor '{distributor_code}'. "
                f"The uploaded file needs a 'Sales Route' column for this view."
            ),
        )

    beats_by_name: dict[str, list[Outlet]] = {}
    for outlet in outlets:
        beats_by_name.setdefault(outlet.original_beat_name, []).append(outlet)

    beat_payload = [
        OriginalBeatOut(
            beat_name=beat_name,
            total_outlets=len(group),
            outlets=[
                OriginalOutletOut(
                    retailer_code=o.retailer_code,
                    name=o.name,
                    latitude=o.latitude,
                    longitude=o.longitude,
                )
                for o in group
            ],
        )
        for beat_name, group in sorted(beats_by_name.items())
    ]

    return OriginalBeatsResponse(
        distributor_code=distributor_code,
        distributor_name=distributor.name,
        beats=beat_payload,
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Sales Beat Optimization API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves generated files (e.g. beats_map_{distributor_code}.html) for the frontend iframe.
Path("output").mkdir(exist_ok=True)
app.mount("/output", StaticFiles(directory="output"), name="output")

app.include_router(router)
