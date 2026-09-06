# Airlines Use-Case — Data Engineering Pipeline

A complete end-to-end data pipeline for flight operations analytics, covering ingestion,
cleaning, transformation, validation, aggregation, and Tableau reporting.

---

## Repository Structure

```
airlines_project/
├── pipeline/
│   ├── airlines_pipeline.py          # Production Python pipeline script
│   └── airlines_pipeline.ipynb       # Clean Jupyter notebook version
├── cleaned_data/
│   ├── fact_flight_operations.csv    # Central fact table (1,040 rows)
│   ├── dim_flights.csv               # Cleaned flight dimension (1,005 rows)
│   ├── dim_bookings.csv              # Cleaned booking dimension (1,000 rows)
│   ├── dim_passengers_masked.csv     # PII-masked passenger dimension (1,039 rows)
│   ├── kpi_overview.csv              # Dashboard header KPIs
│   ├── kpi_by_airline.csv            # Airline-level aggregations
│   ├── kpi_by_route.csv              # Route-level aggregations
│   └── data_quality_log.csv          # DQ audit trail (10 rules)
├── docs/
│   └── Airlines_Pipeline_Documentation.pdf   # Full technical documentation
└── README.md
```

---

## Architecture

The pipeline follows a **Medallion Architecture**:

```
┌─────────────────────────────────────────────────────────┐
│  BRONZE (Raw Ingestion)                                  │
│  ├── UseCase_-_Airlines.xlsx  →  raw_flights             │
│  ├── dim_bookings.csv         →  raw_bookings            │
│  └── dim_passengers_masked.csv→  raw_passengers          │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  SILVER (Cleaned & Validated)                            │
│  ├── dim_flights.csv           (10 DQ rules applied)    │
│  ├── dim_bookings.csv          (status normalised)      │
│  └── dim_passengers_masked.csv (PII restricted)         │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│  GOLD (Fact Table + KPIs)                                │
│  ├── fact_flight_operations.csv (LEFT JOIN, payments)   │
│  ├── kpi_overview.csv                                   │
│  ├── kpi_by_airline.csv                                 │
│  ├── kpi_by_route.csv                                   │
│  └── data_quality_log.csv                               │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
              Tableau Report / Dashboard
```

---

## Data Flow

```
Excel (flights)  ──┐
CSV  (bookings)  ──┼──► Ingest ──► Clean ──► Transform ──► Join ──► Aggregate ──► Export
CSV  (passengers)──┘
```

**Step-by-step:**

1. **Ingest** raw files; parse datetimes; convert duration HH:MM:SS → minutes
2. **Clean flights** — 10 DQ rules (dedup, ID validation, timestamp checks, overnight detection)
3. **Clean bookings** — status normalisation, booking_date parsing
4. **Clean passengers** — column restriction to 4 PII-safe fields
5. **Transform** — derive `route`, `is_confirmed`, `overnight_flight`, recalculate `duration_minutes`
6. **Join** — bookings LEFT JOIN flights; tag unmatched bookings; add payment amounts
7. **Aggregate** — compute KPIs at overview, airline, and route levels
8. **Export** — 8 UTF-8 CSV files ready for Tableau import

---

## 🛠️ How to Run

### Requirements
```bash
pip install pandas numpy openpyxl
```

### Run the pipeline
```bash
python pipeline/airlines_pipeline.py
```

### Run as Jupyter notebook
```bash
jupyter notebook pipeline/airlines_pipeline.ipynb
```

All outputs are written to `cleaned_data/`. A timestamped log file is created in `logs/`.

---

## 🧹 Data Quality Rules

| Rule | Rows Affected |
|---|---|
| Missing airline labelled Unknown | 41 |
| Exact duplicate rows removed | 15 |
| Malformed flight identifiers rejected | 0 |
| Same-origin/destination rejected | 0 |
| Missing timestamps rejected | 0 |
| Overnight arrival dates corrected | 1 |
| Invalid duration rejected | 0 |
| Conflicting schedules rejected | 0 |
| Invalid booking status → UNKNOWN | 75 |
| Unmatched bookings tagged REJECTED | 0 |

---

## Key KPIs

| KPI | Value |
|---|---|
| Total Bookings | 1,000 |
| Confirmed Bookings | ~320–334 |
| Valid Flight Records | 998–1,005 |
| Average Flight Duration | ~164–165 min |
| Overnight Flights | 1 |
| Total Payment Revenue | ₹7.4M – ₹8.9M |

---

## Data Governance

- **PII Protection:** Only 4 columns retained from the passenger table. No names, contacts, or national IDs are ever loaded into memory.
- **Audit Trail:** Every DQ rule is logged to `data_quality_log.csv` with the affected row count.
- **Reproducibility:** All outputs are deterministic. The payment simulation uses a fixed seed (42).
- **Idempotency:** Running the pipeline multiple times with the same source produces identical results.

---

## Data Model (Star Schema)

```
dim_passengers_masked
       │ passenger_id
       │
       v
fact_flight_operations ──> dim_flights
       ^                       flight_id
       │
dim_bookings
  booking_id
```

---

## Documentation

Full technical documentation — including architecture diagram, data flow, data model,
assumptions, cleaning logic, and transformation steps — is provided in:

`docs/Airlines_Pipeline_Documentation.pdf`

---

## Technology Stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Data processing | pandas, numpy |
| Excel parsing | openpyxl |
| Notebook | Jupyter (nbformat) |
| Documentation | python-docx (via docx npm) |
| Logging | Python standard `logging` |
| Portability | Compatible with Databricks, AWS Glue, Azure Data Factory |
