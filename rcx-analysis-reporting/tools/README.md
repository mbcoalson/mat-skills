# ECM/FIM Calculation Tools

Reusable Python scripts that generate Excel workbooks with **working Excel formulas** for energy conservation measure (ECM) savings calculations.

## Prerequisites

- Python 3.12+ with `openpyxl` installed
- LibreOffice (optional, for formula recalculation via `recalc.py`)

## Tools

### ecm-motor-savings.py
Motor-hour-based savings: pump interlocking, fan scheduling, equipment scheduling.

```bash
# Quick single equipment
py -3.12 ecm-motor-savings.py --name "P-7" --hp 5 --current-hrs-wk 168 --proposed-hrs-wk 84 --elec-rate 0.10 -o savings.xlsx

# Multi-equipment with scenarios (JSON config)
py -3.12 ecm-motor-savings.py --config project-inputs.json -o savings.xlsx
```

### ecm-economizer-savings.py
Economizer displacement savings with optional monthly hour breakdown.

```bash
# Quick single unit
py -3.12 ecm-economizer-savings.py --name "AHU-1" --tons 25 --part-load 0.30 --displacement 0.50 --econ-hours 1950 -o econ.xlsx

# Multi-unit with monthly hours (JSON config)
py -3.12 ecm-economizer-savings.py --config project-inputs.json -o econ.xlsx
```

### ecm-simhtgclg-savings.py
Simultaneous heating/cooling dual-fuel waste with valve characteristic corrections.

```bash
# Quick single unit
py -3.12 ecm-simhtgclg-savings.py --name "RTU-2" --htg-mbh 23.28 --hw-valve-pos 0.419 --sim-fraction 0.482 -o simhtgclg.xlsx

# Full config with valve scenarios (JSON config)
py -3.12 ecm-simhtgclg-savings.py --config project-inputs.json -o simhtgclg.xlsx
```

## Output Conventions

| Element | Style |
|---------|-------|
| User-editable inputs | Blue text, yellow background |
| Formulas | Black text |
| Cross-sheet references | Green text |
| Warnings / verification flags | Red bold text |
| Notes | Gray italic text |

All calculation cells contain **Excel formulas** referencing an Assumptions sheet. Change an assumption and all calculations update automatically.

## JSON Config Format

Each tool accepts `--config path.json`. See the docstring at the top of each script for the full JSON schema. Key pattern:

```json
{
  "project": "Project Name",
  "ecm_id": "ECM-XX",
  "ecm_title": "Description",
  "elec_rate": 0.10,
  "equipment": [ ... ],
  "verifications": ["Field check item 1"]
}
```

## Shared Module

`_styles.py` contains all color/font/format constants and helper functions shared across tools.
