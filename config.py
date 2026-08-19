import os

from dotenv import load_dotenv

load_dotenv()

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/sales_beat")

# Beat optimization defaults
MIN_OUTLETS_PER_BEAT = int(os.getenv("MIN_OUTLETS_PER_BEAT", 25))
MAX_OUTLETS_PER_BEAT = int(os.getenv("MAX_OUTLETS_PER_BEAT", 35))

# Frontend origin(s) allowed to call the API (comma-separated). "localhost" and
# "127.0.0.1" are different origins as far as CORS/browsers are concerned, even
# though they resolve to the same machine, so both are allowed by default.
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]

# Road-routing backend for map polylines (OSRM-compatible /route/v1 API).
# Default is the public OSRM demo server: free, no API key, but rate-limited and
# explicitly not intended for production traffic. Point this at a self-hosted OSRM
# instance (or another OSRM-compatible host) before going live at scale.
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org/route/v1/driving")

# Same OSRM backend's Table service, used to get a real road-distance matrix
# for TSP route ordering (instead of straight-line distance, which can pick
# orders that look short but require a big detour around rivers/highways).
OSRM_TABLE_BASE_URL = os.getenv(
    "OSRM_TABLE_BASE_URL", "https://router.project-osrm.org/table/v1/driving"
)
