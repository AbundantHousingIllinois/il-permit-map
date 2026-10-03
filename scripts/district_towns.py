#!/usr/bin/env python3
"""The two towns on each district's printable sheet (ROADMAP item 4).

Rules, as Austin Busch set them on 2026-10-02 and Steffany confirmed:

1. **The district office town comes first**, when the member's district office
   is in a municipality inside the district. The office city is read from the
   Open States address ("..., Berwyn, IL 60609") and matched by exact name to a
   municipality in the district's list (``L.DISTRICT_SHARE_MIN`` of its land
   or more inside). No geocoding.
2. **Otherwise the sheet shows the two best towns.** That covers a member with
   no address listed, an address in Springfield (a capitol office, not a
   district one), and Sosnowski (House 69), whose office in Machesney Park is
   outside his district.
3. **"Best" is ranked in two tiers.** First, towns with more than half their land
   in the district and more than 5,000 people; then every other town. Within a
   tier, by land share x 2020 population: roughly, how many of the town's
   residents live in the district, the assumption the district estimate makes.
   Ties go to the larger land share, then to the name.
4. **Chicago is one town**, never split. A Chicago district shows Chicago and its
   best suburb if it has one, which the ranking gives without a special case.

Only municipalities are candidates. A Census-designated place (``... CDP``) is
unincorporated land with no town government, so it is never featured.

``choose()`` returns the picks with a plain-English reason for each, and the
whole ranked candidate list, so the choice can be checked by hand. build.py
writes both to ``data/processed/`` as CSV.
"""

from __future__ import annotations

import re

TIER1_SHARE = 0.5           # more than half the town's land inside the district
TIER1_POP = 5000            # and more than 5,000 people (2020 Census)

_OFFICE_CITY = re.compile(r",\s*([^,]+?),\s*IL\s+\d{5}")


def office_city(address: str | None) -> str | None:
    """"6729 Stanley Ave. 1st Floor, Suite A, Berwyn, IL 60609" -> "Berwyn"."""
    m = _OFFICE_CITY.search(address or "")
    return m[1].strip() if m else None


def is_municipality(namelsad: str) -> bool:
    return not namelsad.endswith(" CDP")


def _score(p: dict) -> float:
    return p["share"] * (p["pop2020"] or 0)


def _tier(p: dict) -> int:
    return 1 if p["share"] > TIER1_SHARE and (p["pop2020"] or 0) > TIER1_POP else 2


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def rank(places: list[dict]) -> list[dict]:
    """Municipal candidates, best first, each with its tier and score."""
    cands = [dict(p, tier=_tier(p), score=round(_score(p)))
             for p in places if is_municipality(p["namelsad"])]
    cands.sort(key=lambda p: (p["tier"], -p["score"], -p["share"], p["name"]))
    for i, p in enumerate(cands, 1):
        p["rank"] = i
    return cands


def _why_ranked(p: dict, label: str) -> str:
    people = f"{_fmt_pct(p['share'])} of its land × {p['pop2020'] or 0:,} people = {p['score']:,}"
    if p["tier"] == 1:
        return (f"{label} among towns with more than half their land in the district "
                f"and more than {TIER1_POP:,} people, by land share × population "
                f"({people}).")
    return (f"{label} by land share × population ({people}). No remaining town has "
            f"more than half its land in the district and more than {TIER1_POP:,} people.")


def office_status(address: str | None, city: str | None, in_district: bool) -> str:
    if not address:
        return "none listed"
    if city and city.lower() == "springfield" and "stratton" in address.lower():
        return "capitol office"
    return "in district" if in_district else "outside district"


def choose(places: list[dict], member: dict | None) -> dict:
    """The sheet's towns for one district.

    ``places`` are the district's rows from districts.json, each with
    ``geoid, name, namelsad, share, pop2020``. Returns ``{"office": {...},
    "towns": [pick, pick], "ranked": [...]}``; ``towns`` has fewer than two
    entries only when the district has fewer than two municipalities.
    """
    member = member or {}
    address = member.get("office")
    city = office_city(address)
    ranked = rank(places)
    office_town = next((p for p in ranked if city and p["name"].lower() == city.lower()),
                       None)
    status = office_status(address, city, office_town is not None)
    picks: list[dict] = []
    if office_town is not None:
        picks.append(dict(office_town, role="office",
                          why=f"The member's district office is here ({address})."))
    else:
        why_no = {"none listed": "No district office address is listed",
                  "capitol office": "The only office listed is a Springfield capitol "
                                    "office, not a district office",
                  "outside district": f"The district office, in {city}, is outside "
                                      "the district"}[status]
    for p in ranked:
        if len(picks) == 2:
            break
        if any(q["geoid"] == p["geoid"] for q in picks):
            continue
        if office_town is not None:
            why = _why_ranked(p, "Best town after the office town")
        elif not picks:
            why = (f"{why_no}, so the sheet shows the two best towns. "
                   + _why_ranked(p, "Best town"))
        else:
            why = _why_ranked(p, "Second-best town")
        picks.append(dict(p, role="best", why=why))
    return {"office": {"address": address, "city": city, "status": status,
                       "geoid": office_town["geoid"] if office_town else None},
            "towns": picks, "ranked": ranked}
