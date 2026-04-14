"""Deterministic sample-data generator for the unified dashboard.

Produces covalent.json, tronox.json, csbp.json with realistic name overlap
so same-company retention and cross-company carry-over are both non-zero and
distinguishable. Intended to be replaced when real source-dashboard exports
are wired in; see README.md.
"""
import json, random, pathlib, datetime as dt

random.seed(42)

ROLES = ["Boilermaker", "Scaffolder", "Rigger", "Welder", "Pipefitter",
         "Electrician", "Mechanical Fitter", "Supervisor", "Safety Officer"]

FIRST = ["James","John","Robert","Michael","David","William","Richard","Joseph",
         "Thomas","Charles","Daniel","Matthew","Anthony","Mark","Paul","Steven",
         "Andrew","Kenneth","Kevin","Brian","George","Edward","Ronald","Timothy",
         "Jason","Jeffrey","Ryan","Jacob","Gary","Nicholas","Eric","Jonathan",
         "Stephen","Larry","Justin","Scott","Brandon","Benjamin","Samuel","Frank",
         "Gregory","Raymond","Alexander","Patrick","Jack","Dennis","Jerry","Tyler",
         "Aaron","Jose","Adam","Henry","Nathan","Douglas","Zachary","Peter",
         "Kyle","Walter","Ethan","Jeremy","Harold","Keith","Christian","Roger",
         "Noah","Gerald","Carl","Terry","Sean","Austin","Arthur","Lawrence",
         "Dylan","Jesse","Jordan","Bryan","Billy","Joe","Bruce","Gabriel"]

LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
         "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson",
         "Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson",
         "White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson","Walker",
         "Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores",
         "Green","Adams","Nelson","Baker","Hall","Rivera","Campbell","Mitchell",
         "Carter","Roberts","Gomez","Phillips","Evans","Turner","Diaz","Parker",
         "Cruz","Edwards","Collins","Reyes","Stewart","Morris","Morales","Murphy",
         "Cook","Rogers","Gutierrez","Ortiz","Morgan","Cooper","Peterson","Bailey",
         "Reed","Kelly","Howard","Ramos","Kim","Cox","Ward","Richardson","Watson"]

def mkname(i):
    random.seed(1000 + i)
    return f"{random.choice(FIRST)} {random.choice(LAST)}"

# Build a worker pool: each worker has a fixed preferred role.
# Partition into permanents per company + floats + casuals so retention/carry-over
# metrics come out at realistic levels.
POOL = []
for i in range(140):
    POOL.append({"name": mkname(i), "role": ROLES[i % len(ROLES)]})

# Reset RNG for shutdown composition
random.seed(42)

PERM = {
    "covalent": POOL[0:14],     # return every Covalent shutdown
    "tronox":   POOL[14:28],    # return every Tronox shutdown
    "csbp":     POOL[28:42],    # return every CSBP shutdown
}
FLOAT = POOL[42:82]             # rotate across companies (cross-company carry-over)
CASUAL = POOL[82:140]           # appear once, drive "new hire" counts

TODAY = dt.date(2026, 4, 14)   # anchor for completed/booked split (matches session date)

# Each tuple: (id, name, start, end). Anything with start > TODAY is treated as booked.
COMPANIES = [
    {
        "key": "covalent",
        "company": "Covalent",
        "site": "Kwinana",
        "shutdowns": [
            ("covalent-2024-04", "Kwinana Apr 2024", "2024-04-08", "2024-04-26"),
            ("covalent-2024-11", "Kwinana Nov 2024", "2024-11-04", "2024-11-22"),
            ("covalent-2025-06", "Kwinana Jun 2025", "2025-06-02", "2025-06-20"),
            ("covalent-2026-01", "Kwinana Jan 2026", "2026-01-12", "2026-01-30"),
            # --- booked ---
            ("covalent-2026-07", "Kwinana Jul 2026", "2026-07-06", "2026-07-24"),
            ("covalent-2027-02", "Kwinana Feb 2027", "2027-02-08", "2027-02-26"),
        ],
    },
    {
        "key": "tronox",
        "company": "Tronox",
        "site": "Kwinana",
        "shutdowns": [
            ("tronox-2024-03", "Kwinana Mar 2024", "2024-03-11", "2024-03-29"),
            ("tronox-2024-09", "Kwinana Sep 2024", "2024-09-09", "2024-09-27"),
            ("tronox-2025-03", "Kwinana Mar 2025", "2025-03-10", "2025-03-28"),
            ("tronox-2025-10", "Kwinana Oct 2025", "2025-10-06", "2025-10-24"),
            # --- booked ---
            ("tronox-2026-05", "Kwinana May 2026", "2026-05-11", "2026-05-29"),
            ("tronox-2026-11", "Kwinana Nov 2026", "2026-11-02", "2026-11-20"),
        ],
    },
    {
        "key": "csbp",
        "company": "CSBP",
        "site": "Kwinana",
        "shutdowns": [
            ("csbp-2024-05", "Kwinana May 2024", "2024-05-13", "2024-05-31"),
            ("csbp-2025-02", "Kwinana Feb 2025", "2025-02-10", "2025-02-28"),
            ("csbp-2025-08", "Kwinana Aug 2025", "2025-08-11", "2025-08-29"),
            ("csbp-2026-02", "Kwinana Feb 2026", "2026-02-09", "2026-02-27"),
            # --- booked ---
            ("csbp-2026-08", "Kwinana Aug 2026", "2026-08-10", "2026-08-28"),
            ("csbp-2027-03", "Kwinana Mar 2027", "2027-03-08", "2027-03-26"),
        ],
    },
]

def compose_roster(company_key, idx):
    """Roster = all permanents + some floats (some overlap with prior shutdowns) + some casuals."""
    perms = list(PERM[company_key])
    # Floats: pick ~12; to create cross-company carry-over, the same float can be
    # picked by different companies in nearby shutdowns. We draw deterministically
    # by a (company, shutdown_index) seed so overlap is controllable.
    rng = random.Random(hash((company_key, idx)) & 0xFFFFFFFF)
    floats = rng.sample(FLOAT, 12)
    # Casuals: fresh each shutdown
    casual_rng = random.Random(hash((company_key, idx, "casual")) & 0xFFFFFFFF)
    casuals = casual_rng.sample(CASUAL, 6)
    return perms + floats + casuals

def by_role_counts(roster):
    counts = {}
    for w in roster:
        counts[w["role"]] = counts.get(w["role"], 0) + 1
    return counts

GENERATED_AT = "2026-04-13T09:00:00Z"

out_dir = pathlib.Path(__file__).parent

for co in COMPANIES:
    shutdowns_out = []
    for idx, (sid, sname, sstart, send) in enumerate(co["shutdowns"]):
        start_d = dt.date.fromisoformat(sstart)
        is_booked = start_d > TODAY

        # Pretend we composed the full roster first (target headcount), then
        # decide how much of it is actually "confirmed" for booked shutdowns.
        full_roster = compose_roster(co["key"], idx)
        full_filled = by_role_counts(full_roster)

        # Required = full roster inflated by a small, per-role shortfall.
        req_rng = random.Random(hash((co["key"], idx, "req")) & 0xFFFFFFFF)
        required = {}
        for role, n in full_filled.items():
            shortfall = 1 if req_rng.random() < 0.3 else 0
            required[role] = n + shortfall

        if is_booked:
            # Near-term bookings (<120 days out): ~50% confirmed.
            # Far-term bookings: ~10% confirmed.
            days_out = (start_d - TODAY).days
            confirm_pct = 0.50 if days_out < 120 else 0.10
            confirm_rng = random.Random(hash((co["key"], idx, "confirm")) & 0xFFFFFFFF)
            roster = [w for w in full_roster if confirm_rng.random() < confirm_pct]
            filled = by_role_counts(roster)
            status = "booked"
        else:
            roster = full_roster
            filled = full_filled
            status = "completed"

        shutdowns_out.append({
            "id": sid,
            "name": sname,
            "site": co["site"],
            "start_date": sstart,
            "end_date": send,
            "status": status,
            "required_by_role": required,
            "filled_by_role": filled,
            "roster": [{"name": w["name"], "role": w["role"]} for w in roster],
        })

    payload = {
        "company": co["company"],
        "generated_at": GENERATED_AT,
        "shutdowns": shutdowns_out,
    }
    (out_dir / f"{co['key']}.json").write_text(json.dumps(payload, indent=2))
    print(f"Wrote {co['key']}.json: {len(shutdowns_out)} shutdowns, "
          f"total roster heads {sum(len(s['roster']) for s in shutdowns_out)}")
