# Hand-maintained data

Hand-entered or hand-supplied inputs. The build reads these files as-is; the one
exception to "no script writes here" is `ahpaa.csv`, below.

## `ahpaa.csv`

Affordable Housing Planning and Appeal Act (AHPAA) status and affordable housing
share by municipality, from the Illinois Housing Development Authority (IHDA).

**This is the one file here that a script writes.** IHDA's AHPAA report has no
API. Impact for Equity supplied IHDA's 2023 report as
`2023-AHPAA-Local-Government-Data.xlsx` (in this directory), and
`scripts/import_ahpaa.py` transcribes its second tab into `ahpaa.csv`. On
2026-10-01 every row was checked against IHDA's published
[2023 Statewide Affordability List](https://www.ihda.org/wp-content/uploads/2023/12/2023-AHPAA-Statewide-Affordability-List.pdf)
and all matched. Never fill a row from memory: a wrong status attached to a
named municipality in testimony is a serious error.

To load a new report: replace the spreadsheet, update `AHPAA_XLSX`,
`AHPAA_SOURCE_URL` and `AHPAA_AS_OF` in `scripts/bps_layout.py`, check the new
file against IHDA's PDF, and run `uv run scripts/import_ahpaa.py`. It stops if a
name no longer matches a Census municipality exactly.

### Columns

| Column | Meaning |
|---|---|
| `geoid` | 7-character Census place GEOID (`17` + 5-digit FIPS place code), e.g. `1782075` for Wilmette. The join uses this, not the name. |
| `municipality` | Name as IHDA publishes it, so a human can verify the row. |
| `status` | IHDA determination, verbatim: `Exempt` or `Non-Exempt`. |
| `source_url` | The IHDA document the rows were checked against. |
| `as_of_date` | Year (`YYYY`) or date (`YYYY-MM-DD`) of the IHDA report — not the date the row was written. The site displays it. |
| `notes` | Anything a reader needs in order to trust the row (e.g. IHDA's Sandoval footnote). |
| `affordable_share` | IHDA's affordable housing share, a fraction (`0.048485` = 4.8%). |
| `affordable_units` | IHDA's count of affordable year-round units (fractional, as IHDA computes it). |
| `year_round_units` | IHDA's count of year-round units. |

### What the build does with it

- **Zero rows** → `ahpaa_status` and `affordable_share` are `null` everywhere and
  the site disables both AHPAA switches with a tooltip explaining why.
- **Rows present** → each `geoid` gets its status and share; places absent from
  the file stay `null` (unknown), which is never rendered as "exempt". `notes`
  is printed in the municipality's panel. `under25` (under 25% affordable, more
  than 2,000 people by the 2020 Census) is derived from the share.

The importer never changes IHDA's status. Where a row needs explaining — Timberlane
is non-exempt on IHDA's ACS population of 1,323 but counted 906 in 2020 — the
explanation goes in `notes` via `NOTES` in `scripts/import_ahpaa.py`.

A `geoid` that does not exist in the built data, or a row that differs from the
spreadsheet, is reported by `scripts/check_data.py` (checks 10 and G).

## `legislator_overrides.csv`

Hand corrections to the legislator snapshot. **Ships with headers and zero rows;
that is the normal state.**

The legislators themselves come from the Open States bulk file, fetched by
`scripts/fetch_districts.py` into `data/raw/legislators/il.csv` with its
provenance in `data/SOURCES.md`. That file is a download, not hand-maintained,
so it lives in `data/raw/`. If it is wrong about a member — a vacancy, a new
appointment, a changed district office phone — add a row here rather than
editing the snapshot, which the next fetch would overwrite.

As with AHPAA: never fill a row from memory. A wrong name or phone number
attached to a district is exactly the error a legislator meeting cannot absorb.

### Columns

| Column | Meaning |
|---|---|
| `chamber` | `senate` or `house`. |
| `district` | District number, e.g. `28`. |
| `field` | The field to replace: `name`, `party`, `email`, `phone`, `url`, or `vacant` (value `true` blanks the member). |
| `value` | The corrected value, verbatim. |
| `source_url` | Where it was read, e.g. the member's ilga.gov page. |
| `as_of_date` | Date the source says it is current as of (`YYYY-MM-DD`). |
| `notes` | Anything a reader needs in order to trust the row. |

An override naming a chamber, district or field that does not exist fails
`scripts/check_data.py` rather than being silently dropped.
