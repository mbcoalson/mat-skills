---
name: rcx-analysis-reporting
description: Retro-commissioning (RCx) analysis workflows including phased ECM/FIM discovery with upfront consolidation, energy savings quantification in Excel, ROM cost estimation, implementation priority matrices, and report assembly following ASHRAE Guideline 0. Use when performing system-by-system RCx investigation, building ECM/FIM registers, calculating savings, estimating costs, or assembling RCx reports.
---

# RCx Analysis & Reporting

Phased workflow for retro-commissioning analysis projects — from equipment investigation through final report delivery. Covers ECM/FIM discovery, savings quantification, cost estimation, and report assembly.

**For SOO development, functional testing, and general Cx procedures:** see `commissioning-reports` skill.

---

## RCx Workflow Architecture

Each phase runs in a clean context window with structured knowledge capture. Session outputs are saved as markdown files that feed subsequent phases.

### Phase 1: Equipment Analysis & SOO Development

Covers both new and existing equipment. Goal: build the data foundation for ECM/FIM discovery.

**Equipment Analysis (New or Existing):**
- Collect equipment schedules from mechanical drawings and submittals
- Site walk data collection: nameplate data, field conditions, photos
- BAS trend data request, collection, and initial review
- Document design-vs-current comparisons for each major system

**SOO Analysis & Development:**
- Review existing SOOs from EoR or controls contractor (if any)
- Identify gaps: missing sequences, outdated references, unaddressed modes
- Develop recommended SOOs where EoR drawings contain none (invoke `commissioning-reports` skill)
- Deliver SOOs to project team for review

**Session Architecture:** One session per system or logical group. Output files:
- `equipment/<system>/design-vs-current.md`
- `session-outputs/phase-1-<system>-output.md`
- SOO deliverables (if applicable)

### Phase 2: ECM/FIM Consolidation Rules & Discovery Framework

**BEFORE starting system-by-system discovery**, establish consolidation rules that govern how findings are organized. This prevents list bloat and eliminates rework at the costing phase.

**Consolidation Rules (define upfront):**

| Rule | Example | Treatment |
|------|---------|-----------|
| Same root cause across multiple systems | Economizer locked on all 4 AHUs | One ECM (building-wide) |
| Equipment being replaced | RTU findings when replacement is planned | Narrative summary paragraph — owner discretion |
| Same failure mode, same trade | Sensor failures across terminal units | One FIM grouped by coordination owner |
| Already-delivered items | SOOs provided to project team | Appendix reference, not ECM/FIM line item |
| Owner-decision-dependent | Scheduling changes requiring owner input | Flag in list, note "requires owner authorization" |

**Discovery Framework Setup:**
- Create ECM/FIM register template with global numbering scheme
- Define output file naming convention: `phase-2-<N>-<system>-output.md`
- Define "Finding Block Format" block structure for each finding (Findings -> Modified SOO -> Affected Equipment)
- Identify systems to be investigated and session order

### Phase 3: System-by-System ECM/FIM Discovery

One system per session (clean context window). Apply consolidation rules AS items are identified — don't create separate line items for findings that match a consolidation rule.

**Per-Session Workflow:**
1. Read prior session outputs + running ECM/FIM register
2. Review equipment analysis, trend data, and SOOs for this system
3. Identify ALL ECMs (controls/sequence changes) and FIMs (hardware repairs/replacements)
4. Apply consolidation rules: merge into existing items or create new ones
5. Document findings in Finding Block Format blocks
6. Update running ECM/FIM register
7. Save structured output file

**Running Artifacts:**
- `session-outputs/ecm-fim-register.md` — updated after every session
- `session-outputs/phase-2-<N>-<system>-output.md` — per-system discovery output

**Cross-System Interactions:**
Document interactions between systems as they emerge (e.g., upstream AHU issues affecting downstream terminal units). These inform priority assignment in Phase 4.

### Phase 4: Quantification & Cost Estimation

**4.1 — Savings Calculations:**
- Quantify Table A ECM candidates in xlsx with working formulas (see ECM/FIM Calculation Tools below)
- Document methodology, assumptions, and confidence level for each calculation
- Flag verification items that would improve accuracy

**4.2 — Consolidation Review & ROM Costs:**
- Final review of ECM/FIM list against consolidation rules (catch any missed groupings)
- ROM cost estimates by category: BAS programming, mechanical contractor, capital, specialty
- Validate costs against known budgets

**4.3 — Table A (Quantified ECM Savings):**
- Populate savings table for calculable ECMs
- Headline number for executive summary

**4.4 — Table B (Implementation Priority Matrix):**
- All ECMs/FIMs organized by priority (CRITICAL / HIGH / MEDIUM / LOW)
- Impact categories: Energy / Comfort / Artifact Preservation / Equipment Life / Code Compliance
- ROM cost ranges and action owners

### Phase 5: Report Assembly

**Mapping Table:** Create a three-layer cross-reference:
- Report final # -> Register global # -> Phase source file + Phase ID
- Assembly instructions for consolidated items (which source files to merge)

**Report Structure (typical):**
1. Executive Summary (headline savings, key findings, replacement scope overview if applicable)
2. Analysis Results (Table A + Table B)
3. Recommendation Details (brief per-ECM/FIM narratives)
4. Recommended SOOs (appendix or inline, depending on project)
5. RCx Scope of Work (Finding Block Format: Findings -> Modified SOO -> Affected Equipment)
6. Appendices (equipment schedules, trend charts, field report reference)

**Narrative Sections (not in ECM/FIM list):**
- Equipment being replaced: summary paragraph with owner-discretion framing
- Interim improvements: short-term fixes at owner's discretion pending replacement
- Delivered items: reference to appendix

---

## ECM/FIM Calculation Tools

### Principle
All ECM/FIM savings calculations are developed in Excel workbooks with working formulas. Python scripts generate these workbooks using openpyxl. The **xlsx skill MUST be invoked** when creating or modifying these tools.

### Tool Directory
```
.claude/skills/rcx-analysis-reporting/tools/
├── ecm-motor-savings.py          # Motor scheduling/interlock savings
├── ecm-economizer-savings.py     # Economizer displacement savings
├── ecm-simhtgclg-savings.py      # Simultaneous heating/cooling waste
├── _styles.py                    # Shared styling constants and helpers
└── README.md                     # Tool usage documentation
```

### Each Tool Must
1. Accept inputs via **CLI args** (quick one-off) AND **JSON config** (reproducible)
2. Generate .xlsx with **working Excel formulas** (never hardcoded Python math results)
3. Place all assumptions on a dedicated **Assumptions sheet** with yellow-highlighted inputs
4. Use **blue text** for user inputs, **black** for formulas, **green** for cross-sheet refs
5. Flag verification items with **red text** warnings
6. Include a **Validation Column** beside each formula: Python-computed result (static) + check cell (`=IF(ABS(excel-python)<0.01,"✓","MISMATCH")`). This proves the Excel independently reproduces the Python math and catches formula reference errors.
7. Provide **engineering methodology notes** for each calculation section: explain the physical principle (e.g., fan affinity cube law, sensible heat equation, bin-hour method), not just formula shorthand. Target reader: an ME who will not see the Python.
8. Annotate every input cell with a **source tag**: nameplate, drawing reference, utility bill date, ASHRAE table, or "VERIFY — assumed value, confirm with [specific source]". Unverified assumptions in red font; confirmed values in black.

### Invocation Pattern
```bash
# CLI args (quick)
py -3.12 tools/ecm-motor-savings.py --hp 5 --hours-saved 5040 --elec-rate 0.10 --output savings.xlsx

# JSON config (reproducible, multi-equipment)
py -3.12 tools/ecm-motor-savings.py --config project-inputs.json --output savings.xlsx
```

### Tool Status
- ecm-motor-savings.py: **BUILT** — pump interlock, fan scheduling, any motor-hour-based ECM
- ecm-economizer-savings.py: **BUILT** — economizer displacement with monthly hour estimates
- ecm-simhtgclg-savings.py: **BUILT** — simultaneous heating/cooling with valve characteristic corrections
- _styles.py: **BUILT** — shared styling constants and helpers

---

## Critical Corrections

### ECM/FIM List Consolidation — Do It Upfront, Not After Discovery
Establish consolidation rules at the START of the analysis process (Phase 2), not at the costing phase. Rules: same root cause -> one item, equipment being replaced -> narrative paragraph, sensor repairs -> group by trade, delivered items -> appendix reference. In one prior project, 47 items across 10 sessions had to be consolidated to 19 at Phase 4.2, creating unnecessary rework.

### ECM/FIM Calculations Belong in Spreadsheets, Not Markdown
ECM and FIM savings calculations must be developed in Excel workbooks with working formulas — never as hardcoded numbers in markdown. Markdown narratives document logic and assumptions; the .xlsx with live formulas is the auditable, adjustable deliverable. Use the xlsx skill to generate workbooks via Python/openpyxl scripts.

### Trend Data Diagnostics — State Evidence, Not Conclusions
When BAS trend data shows command-vs-status disagreement, do not conclude "equipment failure." The same trend signature could indicate hardware failure, a failed status sensor, or a BAS wiring/communication issue. State what the data shows, list all plausible causes, and prescribe a physical inspection. Compare against peer equipment on the same system.

### Concise Recommendations Over Exhaustive Analysis
Provide the recommendation and a brief note that alternative X was considered but dismissed due to Y. Do NOT write multi-paragraph justifications for every dismissed option. Deliverables should read as confident recommendations, not design decision journals.

### Document Tone — Never Blame Team Members
Frame deliverables positively — what the analysis provides, NOT what someone failed to deliver. Never throw the EoR or any team member under the bus. Do not include internal workflow sections in external deliverables.

### Excel Must Independently Verify Python Calculations
Python analysis tools and Excel Calculations sheets compute energy savings via independent paths. Both must arrive at the same result. Include a Validation Column that displays the Python result beside each Excel formula with an agreement check. Methodology notes must explain engineering basis for a non-Python audience. All assumptions must cite their source or be flagged for field verification.

### TAB vs Cx Responsibility Distinction
Test & Balance (TAB) establishes system setpoints through balancing work. Commissioning (Cx) verifies performance based on TAB's established values. Use "TAB" for setpoint determination, "commissioning" for sequence verification. Apply throughout all deliverables.

## Best Practices

### Reusable Calculation Tools via xlsx Skill
For recurring ECM/FIM calculation types, create individual Python scripts that generate .xlsx workbooks with working formulas. Each tool accepts project-specific variables via CLI args or JSON config. Store tools in `rcx-analysis-reporting/tools/` for reuse across projects. Always invoke the xlsx skill when building or maintaining these tools.
