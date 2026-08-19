import logging
from pathlib import Path
from typing import Union

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "Retailer Code",
    "Retailer Name",
    "Distributor Code",
    "Latitude",
    "Longitude",
    "Town Name",
]

# Present in most master sheets but not strictly required: the outlet's
# pre-existing beat assignment from before this system existed. Kept alongside
# the newly computed beat so the UI can show "old vs new" grouping. Matched
# case/whitespace-insensitively since sheets are inconsistent about casing
# (e.g. "Sales Route" vs "Sales route" vs "SALES ROUTE").
OPTIONAL_COLUMNS = ["Sales Route"]


def _find_column_ci(df: pd.DataFrame, name: str) -> Union[str, None]:
    target = name.strip().casefold()
    for col in df.columns:
        if col.strip().casefold() == target:
            return col
    return None

# Identifier columns must be read as strings, never inferred as numeric — otherwise
# pandas silently strips leading zeros (e.g. "000200122700190" -> 200122700190) and
# codes end up as numpy ints, which later breaks JSON/Pydantic string validation.
CODE_COLUMNS = ["Retailer Code", "Distributor Code"]


class RetailDataLoader:
    """Loads a raw retail master file and produces a clean, validated outlet DataFrame."""

    def __init__(self, file_path: Union[str, Path]):
        self.file_path = Path(file_path)

    def load(self) -> pd.DataFrame:
        df = self._read_file()
        initial_count = len(df)

        df = self._filter_active(df)
        df = self._extract_columns(df)
        df = self._clean_coordinates(df)

        dropped = initial_count - len(df)
        logger.info(
            "RetailDataLoader: %d outlets loaded, %d dropped/invalid, %d retained.",
            initial_count, dropped, len(df),
        )
        return df.reset_index(drop=True)

    def _read_file(self) -> pd.DataFrame:
        suffix = self.file_path.suffix.lower()
        if suffix == ".csv":
            header = pd.read_csv(self.file_path, nrows=0)
            dtype = {col: str for col in CODE_COLUMNS if col in header.columns}
            return pd.read_csv(self.file_path, dtype=dtype)
        if suffix == ".xlsx":
            # openpyxl's read_only mode streams rows instead of loading the whole
            # workbook, which is significantly faster for large sheets. Only
            # applies to .xlsx (openpyxl) — .xls goes through a different engine.
            header = pd.read_excel(self.file_path, nrows=0, engine_kwargs={"read_only": True})
            dtype = {col: str for col in CODE_COLUMNS if col in header.columns}
            return pd.read_excel(self.file_path, dtype=dtype, engine_kwargs={"read_only": True})
        if suffix == ".xls":
            header = pd.read_excel(self.file_path, nrows=0)
            dtype = {col: str for col in CODE_COLUMNS if col in header.columns}
            return pd.read_excel(self.file_path, dtype=dtype)
        raise ValueError(f"Unsupported file type: {suffix}")

    def _filter_active(self, df: pd.DataFrame) -> pd.DataFrame:
        if "Retailer Status" not in df.columns:
            raise ValueError("Missing required column: 'Retailer Status'")

        before = len(df)
        mask = df["Retailer Status"].astype(str).str.strip().str.casefold() == "active"
        filtered = df[mask]
        logger.info(
            "RetailDataLoader: %d/%d outlets are Active.", len(filtered), before
        )
        return filtered

    def _extract_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        # Map each optional column's canonical name to whatever the sheet actually
        # calls it (any casing/spacing), so we can select then rename to canonical.
        optional_actual = {
            name: actual for name in OPTIONAL_COLUMNS
            if (actual := _find_column_ci(df, name)) is not None
        }

        df = df[REQUIRED_COLUMNS + list(optional_actual.values())].copy()
        df = df.rename(columns={actual: name for name, actual in optional_actual.items()})

        for col in CODE_COLUMNS:
            df[col] = df[col].astype(str).str.strip()

        for col in OPTIONAL_COLUMNS:
            if col not in df.columns:
                df[col] = None
            else:
                df[col] = df[col].astype(str).str.strip().replace({"": None, "nan": None})

        return df

    def _clean_coordinates(self, df: pd.DataFrame) -> pd.DataFrame:
        before = len(df)
        df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
        df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")

        valid = (
            df["Latitude"].notna()
            & df["Longitude"].notna()
            & (df["Latitude"] != 0)
            & (df["Longitude"] != 0)
            & df["Latitude"].between(-90, 90)
            & df["Longitude"].between(-180, 180)
        )

        invalid_count = before - int(valid.sum())
        if invalid_count:
            logger.warning(
                "RetailDataLoader: dropped %d outlets with invalid/missing/zero coordinates.",
                invalid_count,
            )
        return df[valid]


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m core.data_loader <path_to_retail_master.csv|.xlsx>")
        sys.exit(1)

    loader = RetailDataLoader(sys.argv[1])
    clean_df = loader.load()
    print(clean_df.head())
