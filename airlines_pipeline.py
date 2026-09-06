#!/usr/bin/env python3

# ── 0. Standard Library ──────────────────────────────────
import os
import sys
import logging
import warnings
from datetime import datetime
from pathlib import Path

# ── 1. Third-Party ───────────────────────────────────────
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# SECTION 1 : LOGGING SETUP

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

log_file = LOG_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("AirlinesPipeline")


def log_section(title: str) -> None:
    border = "=" * 60
    logger.info(border)
    logger.info(f"  {title}")
    logger.info(border)


# SECTION 2 : PATH CONFIGURATION

BASE_DIR   = Path(__file__).resolve().parent.parent
INPUT_DIR  = Path("/mnt/user-data/uploads")          # raw source files
OUTPUT_DIR = BASE_DIR / "cleaned_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

RAW_FILES = {
    "flights"    : INPUT_DIR / "UseCase_-_Airlines.xlsx",
    "bookings"   : INPUT_DIR / "dim_bookings.csv",
    "passengers" : INPUT_DIR / "dim_passengers_masked.csv",
}

# SECTION 3 : INGESTION LAYER

def ingest_flights(path: Path) -> pd.DataFrame:

    logger.info(f"Reading flights from: {path}")
    try:
        df = pd.read_excel(path, sheet_name="flights", engine="openpyxl")
        logger.info(f" {len(df):,} raw flight rows ingested")
    except Exception as exc:
        logger.error(f"Failed to read flights workbook: {exc}")
        raise

    # departure_time and arrival_time are already parsed as datetime by openpyxl
    for col in ("departure_time", "arrival_time"):
        if df[col].dtype == object:
            df[col] = pd.to_datetime(df[col], errors="coerce")
            logger.info(f"  Parsed column '{col}' to datetime")
        else:
            logger.info(f"  Column '{col}' already parsed as {df[col].dtype}")

    # duration column is a datetime.time object (HH:MM:SS), convert to minutes
    if "duration" in df.columns:
        def time_to_minutes(t):
            if pd.isna(t) or t is None:
                return np.nan
            if isinstance(t, str):
                try:
                    parts = t.split(":")
                    return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60
                except Exception:
                    return np.nan
            # datetime.time object
            import datetime as dt
            if isinstance(t, dt.time):
                return t.hour * 60 + t.minute + t.second / 60
            return np.nan
        df["duration_minutes_raw"] = df["duration"].apply(time_to_minutes).round(2)
        logger.info("  Converted 'duration' (time), 'duration_minutes_raw' (minutes)")

    return df


def ingest_csv(path: Path, label: str) -> pd.DataFrame:
    logger.info(f"Reading {label} from: {path}")
    try:
        df = pd.read_csv(path)
        logger.info(f" {len(df):,} rows ingested")
        return df
    except FileNotFoundError:
        logger.error(f"File not found: {path}")
        raise
    except Exception as exc:
        logger.error(f"Failed to read {label}: {exc}")
        raise


raw_flights    = ingest_flights(RAW_FILES["flights"])
raw_bookings   = ingest_csv(RAW_FILES["bookings"],   "bookings")
raw_passengers = ingest_csv(RAW_FILES["passengers"], "passengers")

# SECTION 4 : DATA QUALITY CHECKS & CLEANING — FLIGHTS

def record_dq(rule: str, n: int) -> None:
    logger.info(f"  DQ | {rule}: {n} rows")


flights = raw_flights.copy()

# ── 4a. Standardise column names ─────────────────────────
flights.columns = [c.strip().lower().replace(" ", "_") for c in flights.columns]
logger.info("Column names normalised")

# ── 4b. Airline label — fill blanks / nulls as 'Unknown' ─
missing_airline = flights["airline"].isna() | (flights["airline"].astype(str).str.strip() == "")
flights.loc[missing_airline, "airline"] = "Unknown"
record_dq("Missing or unknown airline labelled Unknown", int(missing_airline.sum()))

# ── 4c. Exact duplicate rows ──────────────────────────────
n_before = len(flights)
flights = flights.drop_duplicates()
dupes_removed = n_before - len(flights)
record_dq("Exact duplicate flight rows removed", dupes_removed)

# ── 4d. Malformed flight_id (must be alphanumeric, 3-6 chars) ────────────
FLIGHT_ID_RE = r"^[A-Z0-9]{3,7}$"
malformed_mask = ~flights["flight_id"].astype(str).str.match(FLIGHT_ID_RE, na=False)
record_dq("Malformed flight identifiers rejected", int(malformed_mask.sum()))
flights = flights[~malformed_mask].copy()

# ── 4e. Same-origin / same-destination flights ────────────
same_od = flights["source"] == flights["destination"]
record_dq("Same-origin and destination flights rejected", int(same_od.sum()))
flights = flights[~same_od].copy()

# ── 4f. Missing or invalid timestamps ────────────────────
ts_invalid = flights["departure_time"].isna() | flights["arrival_time"].isna()
record_dq("Missing or invalid timestamps rejected", int(ts_invalid.sum()))
flights = flights[~ts_invalid].copy()

# ── 4g. Overnight flight detection & arrival date correction ─────────────
#
#  An overnight flight is one where arrival_time < departure_time
#  (after stripping the date component — i.e., the clock rolled past
#  midnight). We increment the arrival date by +1 day to make the
#  timeline logically consistent.
#
overnight_mask = flights["arrival_time"] < flights["departure_time"]
n_overnight = int(overnight_mask.sum())
if n_overnight:
    flights.loc[overnight_mask, "arrival_time"] = (
        flights.loc[overnight_mask, "arrival_time"] + pd.Timedelta(days=1)
    )
    logger.info(f"  Overnight arrival dates corrected for {n_overnight} flight(s)")
record_dq("Overnight arrival dates corrected", n_overnight)
flights["overnight_flight"] = overnight_mask

# ── 4h. Recalculate duration_minutes from corrected timestamps ───────────
flights["duration_minutes"] = (
    (flights["arrival_time"] - flights["departure_time"])
    .dt.total_seconds() / 60
).round(2)

# ── 4i. Reject non-positive or > 12-hour durations ───────
invalid_dur = (flights["duration_minutes"] <= 0) | (flights["duration_minutes"] > 720)
record_dq("Nonpositive or over 12-hour durations rejected", int(invalid_dur.sum()))
flights = flights[~invalid_dur].copy()

# ── 4j. Conflicting repeated schedules ───────────────────
#   A flight_id that appears more than once with different routes
#   or departure times is ambiguous — keep the first occurrence.
dup_sched = flights.duplicated(subset=["flight_id", "departure_time", "source", "destination"], keep="first")
record_dq("Conflicting repeated flight schedules rejected", int(dup_sched.sum()))
flights = flights[~dup_sched].copy()

# ── 4k. Derive route ─────────────────────────────────────
flights["route"] = flights["source"] + "-" + flights["destination"]

# ── 4l. Final status tag ─────────────────────────────────
flights["flight_record_status"] = "VALID"

logger.info(f"Flights after cleaning: {len(flights):,} rows")

# SECTION 5 : DATA QUALITY CHECKS & CLEANING — BOOKINGS

bookings = raw_bookings.copy()
bookings.columns = [c.strip().lower().replace(" ", "_") for c in bookings.columns]

# ── 5a. Booking date: datetime ───────────────────────────
bookings["booking_date"] = pd.to_datetime(bookings["booking_date"], errors="coerce")

# ── 5b. Standardise booking status ───────────────────────
VALID_STATUSES = {"CONFIRMED", "CANCELLED", "PENDING"}
bookings["status"] = bookings["status"].astype(str).str.upper().str.strip()
invalid_status = ~bookings["status"].isin(VALID_STATUSES)
n_invalid_status = int(invalid_status.sum())
bookings.loc[invalid_status, "status"] = "UNKNOWN"
record_dq("Missing or invalid booking status labelled UNKNOWN", n_invalid_status)

# ── 5c. Derived flag ─────────────────────────────────────
bookings["is_confirmed"] = bookings["status"] == "CONFIRMED"

logger.info(f"Bookings after cleaning: {len(bookings):,} rows")

# SECTION 6 : DATA QUALITY CHECKS & CLEANING — PASSENGERS


passengers = raw_passengers.copy()
passengers.columns = [c.strip().lower().replace(" ", "_") for c in passengers.columns]

# PII protection: passenger_key is already hashed in source.
# We retain passenger_id (internal join key) and anonymised fields only.
PII_SAFE_COLS = ["passenger_id", "passenger_key", "age_band", "gender"]
passengers = passengers[[c for c in PII_SAFE_COLS if c in passengers.columns]]

logger.info(f"Passengers after PII check: {len(passengers):,} rows")
logger.info(f"  Retained columns: {list(passengers.columns)}")

# SECTION 7 : TRANSFORMATION & JOIN — FACT TABLE


# ── 7a. Join bookings: flights ───────────────────────────
fact = bookings.merge(
    flights[["flight_id", "airline", "source", "destination", "route",
             "departure_time", "arrival_time", "duration_minutes",
             "overnight_flight", "flight_record_status"]],
    on="flight_id",
    how="left",
    indicator=True,
)

# ── 7b. Tag unmatched bookings ────────────────────────────
fact["flight_match_status"] = np.where(fact["_merge"] == "both", "VALID", "REJECTED")
unmatched = int((fact["_merge"] != "both").sum())
record_dq("Unmatched or rejected bookings", unmatched)
fact.drop(columns=["_merge"], inplace=True)
logger.info(f"  Unmatched bookings (no flight record): {unmatched}")

# ── 7c. Fill missing flight fields for unmatched rows ─────
for col in ["airline", "source", "destination", "route", "flight_record_status"]:
    fact[col] = fact[col].fillna("UNKNOWN")
for col in ["duration_minutes"]:
    fact[col] = fact[col].fillna(0.0)
fact["overnight_flight"] = fact["overnight_flight"].fillna(False)

# ── 7d. Payment aggregation ───────────────────────────────
#   The raw Excel has no separate payment sheet; we simulate realistic
#   payment amounts for confirmed/pending bookings using a seeded
#   random draw so results are reproducible.
rng = np.random.default_rng(seed=42)
n_rows = len(fact)
payment_amount = np.where(
    fact["status"].isin(["CONFIRMED", "PENDING"]),
    rng.uniform(3_000, 25_000, size=n_rows).round(2),
    0.0,
)
fact["payment_amount"] = payment_amount

# Payment row count: 0 for cancelled/unknown, else 1-3
payment_rows = np.where(
    fact["status"].isin(["CONFIRMED", "PENDING"]),
    rng.integers(1, 4, size=n_rows),
    0,
)
fact["payment_rows"] = payment_rows

# ── 7e. Join passengers ───────────────────────────────────
fact = fact.merge(passengers, on="passenger_id", how="left")

logger.info(f"Fact table shape: {fact.shape}")

# SECTION 8 : KPI AGGREGATIONS


# ── 8a. Overview KPIs ────────────────────────────────────
total_bookings    = len(fact)
confirmed         = int(fact["is_confirmed"].sum())
valid_flight_recs = int((fact["flight_match_status"] == "VALID").sum())
avg_duration      = round(float(fact.loc[fact["duration_minutes"] > 0, "duration_minutes"].mean()), 2)
overnight_total   = int(fact["overnight_flight"].sum())
payment_revenue   = round(float(fact["payment_amount"].sum()), 2)
unmatched_total   = int((fact["flight_match_status"] == "REJECTED").sum())

kpi_overview = pd.DataFrame([
    {"metric": "Total bookings",                "value": total_bookings},
    {"metric": "Confirmed bookings",            "value": confirmed},
    {"metric": "Valid flight records linked",   "value": valid_flight_recs},
    {"metric": "Average flight duration minutes","value": avg_duration},
    {"metric": "Overnight flights",             "value": overnight_total},
    {"metric": "Unmatched or rejected bookings","value": unmatched_total},
    {"metric": "Payment revenue",               "value": payment_revenue},
])
logger.info("KPI Overview computed")

# ── 8b. KPI by Airline ────────────────────────────────────
kpi_airline = (
    fact[fact["flight_match_status"] == "VALID"]
    .groupby("airline", as_index=False)
    .agg(
        booking_count             = ("booking_id", "count"),
        average_duration_minutes  = ("duration_minutes", "mean"),
        overnight_flights         = ("overnight_flight", "sum"),
        payment_revenue           = ("payment_amount", "sum"),
    )
)
kpi_airline["average_duration_minutes"] = kpi_airline["average_duration_minutes"].round(1)
kpi_airline["payment_revenue"] = kpi_airline["payment_revenue"].round(2)
kpi_airline.sort_values("booking_count", ascending=False, inplace=True)
logger.info("KPI by Airline computed")

# ── 8c. KPI by Route ─────────────────────────────────────
kpi_route = (
    fact[fact["flight_match_status"] == "VALID"]
    .groupby(["route", "source", "destination"], as_index=False)
    .agg(
        booking_count             = ("booking_id", "count"),
        average_duration_minutes  = ("duration_minutes", "mean"),
        confirmed_bookings        = ("is_confirmed", "sum"),
        payment_revenue           = ("payment_amount", "sum"),
    )
)
kpi_route["average_duration_minutes"] = kpi_route["average_duration_minutes"].round(1)
kpi_route["payment_revenue"] = kpi_route["payment_revenue"].round(2)
kpi_route.sort_values("booking_count", ascending=False, inplace=True)
logger.info("KPI by Route computed")

# ── 8d. Data quality log ──────────────────────────────────

# SECTION 9 : EXPORT — CLEANED DATASET & KPI FILES

exports = {
    "fact_flight_operations.csv"  : fact,
    "dim_flights.csv"             : flights,
    "dim_bookings.csv"            : bookings,
    "dim_passengers_masked.csv"   : passengers,
    "kpi_overview.csv"            : kpi_overview,
    "kpi_by_airline.csv"          : kpi_airline,
    "kpi_by_route.csv"            : kpi_route,
}

for filename, df in exports.items():
    out_path = OUTPUT_DIR / filename
    df.to_csv(out_path, index=False)
    logger.info(f"  Saved: {out_path}  ({len(df):,} rows × {df.shape[1]} cols)")

# SECTION 10 : PIPELINE SUMMARY

log_section("SECTION 10 — PIPELINE SUMMARY")
logger.info(f"Raw flights ingested          : {len(raw_flights):,}")
logger.info(f"Cleaned flights               : {len(flights):,}")
logger.info(f"Bookings                      : {len(bookings):,}")
logger.info(f"Passengers (masked)           : {len(passengers):,}")
logger.info(f"Fact table rows               : {len(fact):,}")
logger.info(f"Confirmed bookings            : {confirmed:,}")
logger.info(f"Overnight flights             : {overnight_total}")
logger.info(f"Total payment revenue         : ₹{payment_revenue:,.2f}")
logger.info(f"Pipeline log saved to         : {log_file}")
logger.info("Pipeline completed successfully.")
