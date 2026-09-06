# Railroad Business Process Wiki — Build Spec v2 (Corrected)

**Archetype:** Union Pacific Railroad (US Class I), built as a *US Class I archetype instantiated with UP public examples*
**Scope:** 15 L1 domains, 290 processes
**PID format:** `RR-{L1}-{L2}-{NN}` (e.g. `RR-04-03-07`)
**Local path:** `~/Projects/rail-wiki/`
**Status:** pre-generation. Nothing generated until the pilot gate clears.

---

## 0. What changed from v1, and why

| # | v1 said | v2 says | Reason |
|---|---|---|---|
| 1 | qwen2.5-coder:14b generates process content | Registry-first: researched fact substrate built and validated *before* any generation; model constrained to registries; automated rejection of ungrounded entities | A 14B coding model has no reliable freight-rail knowledge and will invent systems, CFR cites and job titles fluently. This is the highest-risk item in the plan. |
| 2 | PSR sits under Network Ops & Dispatching | Split into **Service Design & Network Planning** (strategic) and **Dispatching & Train Movement** (tactical) | Railroads separate these organizationally. Collapsing them loses ~20 real processes. |
| 3 | PTC sits under Safety & Regulatory | PTC decomposed across Engineering (wayside + subdivision track data files), Technology (back office server), Dispatching (enroute failure handling), Mechanical (onboard), Safety (FRA reporting only) | PTC is an operating system, not a compliance topic. |
| 4 | "Union agreement rules" as one bullet | Crew District & CBA Rule Administration as a full L2 cluster; HOS split into statutory limits, records, limbo time, rest programs | Crew calling is governed by CBAs that vary *by crew district*. |
| 5 | Signals as a bullet under Engineering | Engineering restructured as Track + Structures + Signals & Communications (32 processes, largest domain) | This is literally how the Engineering department is organized on a Class I. |
| 6 | No hazmat | **Hazardous Materials & Environmental** as its own L1 | Highest-salience topic in the industry post-East Palestine. Its absence would be the first thing a rail person noticed. |
| 7 | "Interchange with other railroads" as a yard bullet | **Interline, Equipment & Car Management** as its own L1 (Railinc/Umler, car hire, empty distribution, trackage rights) | Interline is an industry-wide operating layer, not a yard task. |
| 8 | Pain point: "legacy system fragmentation" | Corrected — UP retired TCS for NetControl in Jan 2024 and is the *least* legacy-bound Class I on core operating systems | Getting this backwards would be an immediate credibility failure in a UP conversation. |
| 9 | No crew-size content | Crew Size Compliance L2, reflecting the FRA 2024 rule upheld on appeal Aug 2026 | Live and material; constrains any automation narrative. |
| 10 | Merger domain generic | Grounded in 49 CFR 1180 machinery + UP-SP 1996 integration failure + current STB procedural posture, explicitly dated and labelled speculative | Live proceeding; content has a shelf life and must say so. |
| 11 | No missing domains flagged | Added: right-of-way/real estate, environmental & fuel, cross-border Mexico, roadway worker protection, demurrage/accessorials, industrial development, railroad police | All are real, sizeable, and were absent. |
| 12 | Per-process fields | Added `regulatory_hook`, `operating_rule_ref`, `kpi_moved`, `confidence`, `sources[]` | Turns the wiki from consulting-speak into something an operator recognizes. |
| 13 | GitHub Pages from a private repo | Flagged as a blocker; see §9 | Pages does not serve private repos on the free plan. |
| 14 | Use UP's real brand palette | Use a *derived* palette + explicit non-affiliation notice; see §10 | Trademark posture, on a company in a live regulatory proceeding. |
| 15 | Pilot = 2 processes | Pilot = registries + 2 deliberately opposite processes + 1 EA diagram + 1 cross-domain flow | 2 processes don't test the two real failure modes (hallucination and blandness). |

---

## 1. Revised L1 taxonomy with weighted allocations

Uniform allocation (24 per domain) is what produces padding in thin domains and compression in fat ones. These weights reflect actual operational mass.

| L1 | Domain | Processes | Notes |
|---|---|---|---|
| 01 | Service Design & Network Planning | 20 | Where PSR actually lives |
| 02 | Dispatching & Train Movement | 22 | Real-time execution only |
| 03 | Terminal & Yard Operations | 26 | |
| 04 | Crew Management | 22 | |
| 05 | Mechanical & Rolling Stock | 24 | |
| 06 | Engineering: Track, Structures & Signals | 32 | Largest — absorbs S&C and PTC field assets |
| 07 | Safety & Regulatory Compliance | 18 | |
| 08 | Hazardous Materials & Environmental | 15 | New |
| 09 | Intermodal & Automotive Operations | 16 | |
| 10 | Commercial, Pricing & Customer Service | 22 | |
| 11 | Interline, Equipment & Car Management | 16 | New |
| 12 | Technology, Data & Cybersecurity | 16 | |
| 13 | Finance, Revenue Accounting & Procurement | 17 | |
| 14 | HR, Labor Relations & Training | 12 | Reduced — certification moved to Crew Mgmt |
| 15 | Merger & Network Integration | 12 | |
| | **Total** | **290** | |

### Ownership rule (prevents duplicate processes)

Three boundaries cause every overlap in a wiki like this. Decide them once, encode them in the generator prompt:

1. **Crew Management vs HR** — anything governed by an operating certification or a crew-district CBA is Crew Management (04). Anything governed by corporate employment policy is HR (14). Engineer/conductor certification is 04, not 14.
2. **Technology (12) vs everything else** — 12 owns the *platform lifecycle* (run, integrate, secure, upgrade). Every other domain owns the *business process that uses* the platform. NetControl availability management is 12; building an outbound train in NetControl is 03.
3. **Safety (07) vs the domain doing the work** — 07 owns rules administration, testing, reporting and regulator liaison. The domain owns the safe execution of its own work. Roadway worker protection is 06, not 07, because the authority is issued and held by Engineering.

---

## 2. L2 structure per domain

### RR-01 · Service Design & Network Planning (20)
Operating Plan Design · Train Symbol & Blocking Plan · Car Trip Plan Management · Terminal Mission Design · Line Capacity & Route Planning · Locomotive Power Distribution Planning · Network Performance Metrics & STB Weekly Performance Reporting

### RR-02 · Dispatching & Train Movement (22)
Territory Dispatching (CTC) · Track Warrant Control / Dark Territory · Authority Issuance & Roadway Worker Coordination · Meet/Pass & Train Prioritization · Enroute Exception Handling (PTC failure, mechanical, weather) · Chief Dispatcher & Network Desk · Slow Order, Heat and Cold Order Management · Emergency & Derailment Dispatch Response

### RR-03 · Terminal & Yard Operations (26)
Inbound Train Processing & Yarding · Hump and Flat Classification · Outbound Train Build & Departure · Local and Industry Switching · Air Brake Tests & Departure Inspection · Yard Planning & Dwell Management · Interchange Delivery and Receipt · Yardmaster Operations · Yard Air, Utility and Ground Support

### RR-04 · Crew Management (22)
Crew Calling & Extra Board · Crew District and CBA Rule Administration · Hours of Service Compliance & Records · Rest, Attendance and Predictable Scheduling Programs · Deadhead and Limbo Time Management · Engineer and Conductor Certification · Territory Qualification & Familiarization · Crew Size Compliance · Tie-up, Lodging and Crew Transportation

### RR-05 · Mechanical & Rolling Stock (24)
Locomotive Running Repair & Servicing · Locomotive Heavy Overhaul and Shop Operations · Railcar Repair & Billing Repair Card Processing · Inbound/Outbound Mechanical Inspection · Wayside Detector Network (hot bearing, wheel impact, acoustic) · Condition-Based Maintenance & Equipment Health Alerts · Fleet Reliability and Failure Analysis · Bad Order Management · Locomotive Fuel Efficiency and Emissions Compliance

### RR-06 · Engineering: Track, Structures & Signals (32)
Visual and Walking Track Inspection · Automated Track Inspection (geometry and rail flaw) · Maintenance-of-Way Programs (rail, tie, surfacing gangs) · Track Authority and Roadway Worker Protection · Bridges and Structures · Signal Design, Installation and Testing · PTC Wayside Assets and Subdivision Track Data File Management · Grade Crossing Signals and FRA Crossing Inventory · Capital Program and Line Capacity Projects · Right-of-Way, Real Estate and Utility Occupancy · Vegetation, Drainage and Environmental Field Work · Derailment Site Restoration

### RR-07 · Safety & Regulatory Compliance (18)
Operating Rules Administration (GCOR) · Efficiency Testing and Operational Observation · Drug and Alcohol Program · Accident and Incident Reporting · FRA Inspection and Enforcement Liaison · Confidential Close Call Reporting · Railroad Police, Trespass and Cargo Security · Grade Crossing Public Safety Outreach · STB Regulatory Filings and Annual Reporting

### RR-08 · Hazardous Materials & Environmental (15)
Hazmat Acceptance, Documentation and Placarding · TIH/PIH Handling and Route Risk Analysis · Key Train Operating Restrictions · Hazmat Training and Certification · Emergency Response Planning and First-Responder Data Access · Tank Car Compliance and Fleet Transition · Environmental Permitting and Site Remediation · Emissions, Fuel and Sustainability Programs · Spill Prevention and Stormwater

### RR-09 · Intermodal & Automotive Operations (16)
Intermodal Gate (ingate/outgate) · Lift and Stack Operations · Intermodal Train Build and Loading Plan · Drayage and Motor Carrier Coordination · Appointment and Terminal Capacity Management · Chassis and Container Pool Management · Port and International Interchange · Automotive Loading, Multilevel Handling and Vehicle Damage · Transload and Bulk Terminal Operations

### RR-10 · Commercial, Pricing & Customer Service (22)
Freight Sales and Account Management · Pricing, Tariffs and Rate Quotation · Contract Management and Common Carrier Obligation · Bid, RFP and Shuttle Train Capacity Auctions · Demurrage and Accessorial Billing · Waybill and Bill of Lading Intake · Customer Service Center and Shipment Tracing · Digital Customer Channels and APIs · Freight Claims, Loss and Damage · Industrial Development and Site Selection · Service Disruption Communication and Recovery

### RR-11 · Interline, Equipment & Car Management (16)
Interchange Agreements and Junction Operations · Industry Equipment Registry (Umler-class) · Car Hire and Per Diem Accounting · Empty Car Distribution and Fleet Allocation · Private and Foreign Car Handling · Short Line and Regional Partner Management · Trackage Rights and Haulage Arrangements · Joint Facilities and Terminal Railroads · Interline EDI Messaging · Cross-Border Interchange and Customs (Mexico/Canada gateways)

### RR-12 · Technology, Data & Cybersecurity (16)
Transportation Management Platform Operations · Computer-Aided Dispatch Platform Operations · PTC Back Office Server Operations · Enterprise Asset Management Platform · Data Platform, Analytics and ML · Field and Mobile Technology · Wayside Telecommunications and ROW Network · Cybersecurity and Rail Security Directives · Integration, API and Partner Connectivity

### RR-13 · Finance, Revenue Accounting & Procurement (17)
Revenue Accounting and Recognition · Freight Billing and Collections · Interline Settlement and Division of Revenue · Cost Accounting and Regulatory Costing · Capital Budgeting and Project Authorization · Fuel Procurement and Hedging · Materials, Supply Chain and Warehousing · Equipment Leasing and Asset Finance · Regulatory Financial Reporting

### RR-14 · HR, Labor Relations & Training (12)
Safety-Sensitive Hiring and Screening · Training Academy and Simulator Programs · Labor Relations under the Railway Labor Act · National and Local Bargaining · Discipline and Investigation Procedure · Workforce Planning, Furlough and Recall · Attendance Policy Administration

### RR-15 · Merger & Network Integration (12)
STB Major Merger Application Process · Operating Plan and Service Assurance Plan · Labor Protective Conditions and Implementing Agreements · Systems Cutover Planning (TMS, CAD, PTC, car accounting) · Reporting Mark and Equipment Registry Transition · Crew District and Dispatch Territory Consolidation · Terminal and Gateway Rationalization · Integration Risk and Historical Lessons · Post-Merger Oversight and Reporting

---

## 3. Per-process schema (revised)

```json
{
  "pid": "RR-06-07-03",
  "name": "Subdivision Track Data File Update Following Track Change",
  "l1": "Engineering: Track, Structures & Signals",
  "l2": "PTC Wayside Assets and Subdivision Track Data File Management",
  "description": "...plain language, 120-200 words...",
  "actors": ["Signal Maintainer", "PTC Field Engineer", "Dispatcher", "Track Supervisor"],
  "inputs": ["Approved track change work order", "Signal circuit plan"],
  "outputs": ["Validated subdivision data file", "Distribution to onboard segments"],
  "systems": [
    {"name": "I-ETMS", "scope": "industry_typical", "source_id": "SYS-014"},
    {"name": "PTC Back Office Server", "scope": "industry_typical", "source_id": "SYS-015"}
  ],
  "regulatory_hook": ["49 CFR 236 Subpart I"],
  "operating_rule_ref": "GCOR (general reference)",
  "kpi_moved": ["PTC initialization success rate", "Enforcement/cutout events"],
  "pain_points": ["..."],
  "confidence": "high | medium | low",
  "sources": ["https://...", "https://..."],
  "last_reviewed": "2026-09-05"
}
```

Three fields are new and non-negotiable:

- **`regulatory_hook`** — the CFR/USC part. Draw from: 213 (track safety), 214 (roadway worker protection), 215 (freight car safety), 217/218 (operating rules, blue flag), 219 (drug & alcohol), 220 (radio communications), 225 (accident reporting), 228 (HOS records), 229 (locomotive safety), 232 (brakes), 234 (crossing signals), 236 (signal & train control incl. PTC), 240 (engineer certification), 242 (conductor certification), 172–174 (hazmat), 1180 (STB mergers).
- **`operating_rule_ref`** — UP is a GCOR railroad. Cite generically unless you can source a specific rule number.
- **`kpi_moved`** — from the standard publicly reported set: train speed by train type, terminal dwell, cars online, dwell at origin, trip plan compliance, first-mile/last-mile performance.

`systems[].scope` must be `company_specific` or `industry_typical`, rendered as a visible badge on the page. That badge is the honesty mechanism for the whole wiki.

---

## 4. Registry-first pipeline (the core architectural fix)

**Do not let the local model name anything.** It fills slots; it does not invent entities.

```
Phase A — Substrate (research, mostly not model-generated)
  registries/systems.json        every system name, with scope flag + source URL
  registries/regulations.json    every CFR/USC cite, with title + source URL
  registries/roles.json          every job title/craft, with source
  registries/kpis.json           metric definitions, with source
  registries/facts.json          any figure or dated event, with source + as_of date

Phase B — Skeleton
  data/taxonomy.json             15 L1 / ~110 L2 / 290 PIDs, names only

Phase C — Generation (constrained)
  generate_rail_wiki.py          prose only; entity slots filled ONLY from registries

Phase D — Validation (hard gate)
  validate_content.py            rejects any process containing:
                                   - a system name not in systems.json
                                   - a CFR cite not in regulations.json
                                   - a role not in roles.json
                                   - a numeric figure not in facts.json
                                 → failures return to Phase C with the offending span
```

**Model choice.** `qwen2.5-coder:14b` is tuned for code, not domain prose — use a general instruct model (`qwen2.5:14b-instruct` or larger if the Mac Mini has headroom) for narrative, and keep the coder model for the generation scripts themselves. Even then, the validator is what makes the output trustworthy, not the model.

**Honest option worth considering:** draft the registries and the ~110 L2 definitions in a frontier model (i.e. here), commit them as reviewed source-of-truth, and let the local pipeline do only expansion and formatting. That plays to the local stack's strength and removes the one thing it can't do.

---

## 5. Diagrams

**EA landscapes:** one per L1 = 15 Mermaid diagrams, AC-wiki style, curved connectors (`%%{init: {'flowchart': {'curve': 'basis'}}}%%`).

**Cross-domain flows (7):**

1. **Carload lifecycle** — order → empty placement → loading → waybill → local pickup → classification → line haul → interchange → destination yard → local delivery → demurrage clock → billing → interline settlement
2. **Crew duty cycle** — call → report → on-duty clock start → line haul → HOS expiry / relief crew → deadhead and limbo time → tie-up → statutory rest → mark up
3. **Track defect** — automated inspection detection → exception classification → slow order into CAD → MOW work order → track authority (Form B) → repair → re-inspection → slow order removal → PTC track data file update
4. **PTC failure enroute** — initialization failure or enroute cutout → dispatcher notification → restricted operation → HOS consequence → back office diagnostics → wayside/onboard root cause → FRA reporting
5. **Bad order car** — wayside detector alert → equipment health system → set out → bad order track → repair or interchange billing → registry update → car hire clock effect
6. **Hazmat unit train** — acceptance and documentation → route risk analysis → key train handling restrictions → detector monitoring → interchange → emergency response scenario
7. **Merger cutover** — dual system operation → reporting mark change → registry and EDI transition → crew district merge → dispatch territory transfer → service assurance monitoring under oversight

Flows 3 and 4 are the two that prove the wiki understands rail. Build them in the pilot.

---

## 6. Grounded fact sheet (verified, seeds `registries/facts.json`)

These are the anchors. Everything else needs its own source before it enters a process.

- **NetControl.** UP cut over to NetControl on 6 January 2024, replacing the Transportation Control System (TCS), a mainframe platform it had run for over 50 years. UP describes itself as the first Class I to modernize all three core operating platforms — PTC, computer-aided dispatch (CADx), and transportation management. Sources: up.com press material, Progressive Railroading (Mar 2024).
  → **Kills the "legacy fragmentation" pain point for UP.** The real story is integration debt around a new service-oriented core, and the contrast with peers still on older platforms.
- **Harriman Dispatching Center**, Omaha — opened 1989, centralized dispatching for the network.
- **Crew size.** The Eleventh Circuit upheld FRA's 2024 two-person crew rule 2-1 on 11 August 2026, rejecting challenges from AAR, ASLRRA and six railroads including UP. The rule stands; the case is not necessarily fully closed.
- **HOS.** Statutory under 49 U.S.C. §21103 as amended by the Rail Safety Improvement Act of 2008 — 12 hours maximum on duty, 10 hours undisturbed rest, consecutive-start limits. Records under 49 CFR 228. "Limbo time" (deadhead/transport time) is a genuine live dispute, not an invented pain point.
- **Labor law.** Rail labor is governed by the **Railway Labor Act**, not the NLRA — Section 6 notices, National Mediation Board, cooling-off periods, Presidential Emergency Boards, potential Congressional intervention. Unions: SMART-TD, BLET, BMWED, IBEW, TCU.
- **Merger posture (as of Sept 2026, date-stamp this).** STB rejected the first application in January 2026 as incomplete; UP/NS refiled 30 April 2026; STB accepted the revised application 28 May 2026 and held proceedings in abeyance pending supplemental information; the railroads completed supplemental filings in July 2026; STB has since resumed the review with a projected timeline running through at least 28 May 2027. Deal value reported at ~$85B. **No approval has been granted.**
- **Historical anchor for RR-15.** The 1996 UP-SP merger produced a severe service meltdown centred on Houston and drew STB emergency service intervention. This is the single most useful, citable case study for merger-integration risk — and it is UP's own history.

---

## 7. Pain points — sourced, not invented

Use these; do not let the model generate new ones.

- PSR-era headcount reductions and the resulting resilience debate
- HOS unpredictability, attendance policies, and limbo time
- Crew availability as a service constraint during demand recovery
- Terminal dwell and trip-plan compliance as competing metrics
- PTC initialization failures and enroute cutouts consuming HOS
- Subdivision track data file currency after MOW changes
- Wayside detector alert volume vs. actionable defect rate
- Demurrage and accessorial disputes as a persistent shipper grievance
- Interline data quality (registry accuracy) affecting car hire settlement
- First-mile/last-mile service consistency for carload shippers
- Merger integration: cutover risk, crew district consolidation, gateway rationalization

Each needs an entry in `facts.json` with a source before use.

---

## 8. Scripts (revised list)

| Script | Change |
|---|---|
| `setup_scaffold.py` | unchanged |
| `build_registries.py` | **NEW** — assembles and validates the five registries, fails on any entry missing a source |
| `build_taxonomy.py` | **NEW** — emits the 15/~110/290 skeleton with PIDs before any prose exists |
| `generate_rail_wiki.py` | rewritten to be registry-constrained; entity slots injected, not generated |
| `validate_content.py` | **NEW** — the hard gate described in §4; exits non-zero on any ungrounded entity |
| `generate_rail_ea_diagrams.py` | unchanged, plus the 7 cross-domain flows |
| `sanitise_mmd.py` | unchanged |
| `build_index.py` | add `scope` badge rendering and `confidence` filter |
| `write_excel.py` | add columns: regulatory_hook, kpi_moved, confidence, source count |
| `build_pptx.py` | unchanged |

`data/processes.json` stays local and gitignored, as before. Tree API batching with the `.deploy` marker as final commit, as before.

---

## 9. Hosting blocker

GitHub Pages does not serve sites from private repositories on the free plan. Your pilot gate requires live URLs. Options, in order of preference:

1. Keep the repo public from the start with a clear non-affiliation notice (§10) — simplest, and the content is all public-source anyway.
2. GitHub Pro/Team for private-repo Pages.
3. Local preview (`python -m http.server`) for the pilot, public repo later — but this breaks the "live URLs" part of your gate.

Decide this before `setup_scaffold.py` runs, because it determines the `.nojekyll` and Pages configuration path.

---

## 10. Branding and legal posture

Different from the Air Canada build, because UP is in a live regulatory proceeding and this artifact may become client-facing.

- **Do not** reproduce UP's trademarked shield or use the exact registered brand palette on a public site.
- **Do** use a rail-industry-appropriate derived palette (deep charcoal, a warm amber accent, a signal-red alert colour) that reads as freight rail without impersonating a specific carrier.
- Add a persistent footer notice: independently compiled from public sources, not affiliated with or endorsed by any railroad, illustrative of US Class I practice.
- Frame the wiki header as **"US Class I Freight Railroad — Business Process Reference"** with UP cited as the primary public example. This survives any merger outcome and makes the asset reusable for BNSF, CSX, NS and CPKC conversations rather than single-client.

---

## 11. Revised pilot gate

Do not generate 290 processes until all five of these exist and you have reviewed them:

1. `registries/systems.json` and `registries/regulations.json` populated for domains 04 and 06 only, every entry sourced
2. **Process A (regulated, citable):** `RR-03-05-01` Initial Terminal Air Brake Test — tests whether the pipeline can hold a precise regulatory process without drifting (49 CFR 232)
3. **Process B (soft, commercial):** `RR-10-02-04` Spot Rate Quotation for a Carload Move — tests whether the pipeline produces something specific rather than generic consulting prose
4. **One EA landscape:** RR-06 Engineering — the most complex domain
5. **One cross-domain flow:** the track defect flow (#3 above) — spans Engineering, Dispatching, and PTC data

Review criterion: hand Process A and the track defect flow to anyone with actual rail operations experience. If they can't find a factual error, the pipeline is safe to scale. If they can, the registry is incomplete, not the model.

---

## 12. Open decisions for you

1. Public repo from day one, or Pro for private Pages? (§9) Private
2. UP-branded or Class I archetype with UP examples? Recommendation: archetype. (§10) archetype
3. Registries drafted in a frontier model and reviewed by you, or attempted locally? Recommendation: frontier draft, local expansion. (§4) 
4. Does the merger domain get a visible "as of" banner and a scheduled re-review date? Recommendation: yes, quarterly.
5. Do you want commodity-specific operating models (grain shuttles, coal unit trains, chemicals, automotive) as an L2 axis inside RR-01 and RR-10, or as a separate reference section? They matter — processes genuinely differ by commodity — but they're currently distributed rather than named.
