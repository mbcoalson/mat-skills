---
name: running-openstudio-models
description: Use this skill when working with OpenStudio 3.10 .osm models to adjust HVAC systems, equipment, thermal zones, schedules, or constructions, then run simulations to validate changes. Handles applying existing measures, running CLI simulations, and saving versioned model files. Delegates to diagnosing-energy-models for simulation failures and writing-openstudio-model-measures for custom measure creation. Includes BCL measure search and download.
---

# Running OpenStudio Models

This skill helps you work with OpenStudio 3.10 `.osm` models to modify building systems, apply measures, and run simulations. It focuses on practical model adjustments and validation runs using the OpenStudio CLI.

# Critical Corrections

### The Deliverable Is a Working .osm That Opens and Runs in OpenStudio Application

**Every model modification session MUST produce an .osm file that the user can open in the OpenStudio Application and run successfully.** No exceptions. No workarounds.

This means:
- **No Python/IDF-only fixes.** If a fix only works via post-translation IDF editing (`_fix_idf_and_run.py`, manual IDF patching), it is NOT complete. The fix must be in the .osm itself.
- **No CLI-only workflows.** The CLI is useful for fast iteration during debugging, but the final deliverable must run from the OS App GUI.
- **Test in the OS App before declaring success.** A model that runs via `openstudio.exe run --workflow` but crashes in the OS App is a broken deliverable.

Common issues that break OS App runs but not CLI:
- Empty `Supply Air Fan Operating Mode Schedule Name` on UnitarySystem (E+ 25.1.0 is stricter)
- Autosized coils that fail when plant loop sizing changes (hard-code UA/capacity in the .osm)
- CW/HW supply branch ordering (fix `OS:Connector:Splitter` / `OS:Connector:Mixer` in the .osm, don't rely on IDF reordering scripts)
- OpenStudioResults measure crashes from Windows MAX_PATH (remove from Measures tab)

**The user works in OpenStudio Application. That is the environment. Everything we produce must work there.**

(Learned: 2026-02-25)

### OS:ThermalZone Volume Must Be Set (Not Just OS:Space)

When hard-coding zone volume to bypass EnergyPlus enclosure calculation failures, you **must** set Volume, Ceiling Height, and Floor Area on the `OS:ThermalZone` object — not just the `OS:Space`. The OpenStudio forward translator maps `OS:ThermalZone` → EnergyPlus `Zone`. Setting these fields on `OS:Space` alone will NOT propagate to the IDF Zone object; the EnergyPlus simulation will still fail with the enclosure severe error.

**Always set on both objects:**
- `OS:Space` — Volume {m3}, Ceiling Height {m}, Floor Area {m2}
- `OS:ThermalZone` — Ceiling Height {m}, Volume {m3}, Floor Area {m2}

(Learned: 2026-02-17)

### Generalize Session Scripts into Reusable Tools

When a Node.js or Python script is written during a session to fix a model problem (e.g., vertex winding reversal, volume hard-coding, bulk thermostat assignment), **generalize it into a callable tool** saved in the skill's `tools/` directory. Future sessions with the same class of problem should invoke the existing tool rather than writing a new one-off script.

**Pattern:** Session script → extract reusable logic → save as `tools/<descriptive-name>.mjs` → document arguments and usage in this skill.

Check `tools/` directory before writing new scripts — the solution may already exist.

(Learned: 2026-02-17)

### WaterHeater:HeatPump — HP Compressor Will Be Silently Disabled If Setpoints Are Shared

When creating `OS:WaterHeater:HeatPump` objects wrapping an `OS:WaterHeater:Mixed`, the HP compressor and backup element **MUST have separate setpoint schedules**. If both reference the same schedule, the backup element's dead band fires first and EnergyPlus permanently disables the HP compressor with a warning:

> `WaterHeater:HeatPump:PumpedCondenser "...": Water heater tank set point temperature is greater than or equal to the cut-in temperature of the heat pump water heater. Heat Pump will be disabled.`

This means ALL DHW energy comes from the resistance backup at COP 1.0. The HP compressor never runs. This warning is easy to miss in large .err files.

**Correct HPWH configuration:**
- `OS:WaterHeater:HeatPump` — Compressor Setpoint Schedule = **60°C** (primary heater), Dead Band = 0.5°C, Control = MutuallyExclusive
- `OS:WaterHeater:Mixed` — Setpoint Schedule = **50°C** (emergency backup only), Dead Band = 2°C

The HP fires at 59.5°C (primary), the backup only fires at 48°C (extreme draw events). Always check `eplusout.err` for "Heat Pump will be disabled" after any HPWH model run.

**Also applies to performance curves**: The capacity and COP biquadratic curves must evaluate to 1.0 at rated conditions. If coefficient1 (constant term) is ~0.37, that does NOT mean the curve evaluates to 0.37 — evaluate the full 6-term biquadratic at (rated_air_WB, rated_condenser_water_T) before diagnosing. Normalize by dividing all coefficients by the evaluation value.

(Learned: 2026-02-27)

### measure.xml Format for OpenStudio 3.10

The `<measure>` root element must **NOT** have an XML namespace attribute. It **MUST** include `<outputs />` and `<provenances />` between `<arguments>` and `<tags>`. Missing these causes `openstudio.exe measure --update` to fail with:
```
Element 'tags': This element is not expected. Expected is ( outputs ).
```

**Correct structure:**
```xml
<measure>
  ...
  <arguments>...</arguments>
  <outputs />
  <provenances />
  <tags>...</tags>
  <files>...</files>
</measure>
```

(Learned: 2026-02-18)

### OpenStudioResults Measure Fails on Windows Long Paths

Copying the `OpenStudioResults` reporting measure to a new working directory will break due to Windows `MAX_PATH` on resource filenames like `Siz.CoilCoolingWaterToAirHeatPumpVariableSpeedEquationFitSpeedData.rb`. The measure will fail to load at runtime with `LoadError: cannot load such file`.

**Fix:** Skip `OpenStudioResults` entirely. Query `eplusout.sql` directly via Python + sqlite3 using `extract-simulation-results.py` or `compare-simulation-runs.py` from the `tools/` directory.

(Learned: 2026-02-18)

### PowerShell for File Operations with Spaces in Paths

Bash shell on Windows cannot reliably handle paths with spaces (e.g., `DD Geometry AL`) even with proper quoting. Write `.ps1` scripts and run with:
```bash
powershell -ExecutionPolicy Bypass -File script.ps1
```

**Never** pass `$variables` inline via `powershell -Command` from bash — they get eaten by bash's own variable expansion. Always use `-File` with a `.ps1` script.

(Learned: 2026-02-18)

### EnergyPlus Unmet Hours SQL Table Structure

The table name is `Time Setpoint Not Met` (not "During Occupied"). The occupied vs total distinction is in the **column name**: `During Occupied Heating` vs `During Heating`. Always explore table and column names with a diagnostic query before writing extraction logic:
```sql
SELECT DISTINCT TableName, ColumnName FROM TabularDataWithStrings
WHERE ReportName = 'SystemSummary' AND TableName LIKE '%Setpoint%'
```

(Learned: 2026-02-18)

### Always Re-Run Baseline for Ground Truth

Design doc values from prior sessions may not match a fresh simulation. For example, fan energy was cited as 2,365 GJ but a fresh v9 run produced 1,992 GJ (the 2,365 was from an earlier model version). **Always re-run the seed model** with an empty-steps workflow to get actual baseline numbers before comparing against a modified run.

(Learned: 2026-02-18)

# Best Practices

### Python 3.12 Explicit Path on This System

The default `python` command may point to a missing Python 3.13 installation. Use the explicit path:
```
C:/Users/mcoalson/AppData/Local/Programs/Python/Python312/python.exe
```
This is also documented in the Tool Notes section under Reusable Tools.

(Learned: 2026-02-18)

# Core Approach

1. **Model Versioning**: Always save new versions before making changes (format: `projectname_YYYY-MM-DD_vX.osm`)
2. **Incremental Changes**: Modify HVAC, zones, schedules, or constructions systematically
3. **Apply Measures**: Use existing measures from BCL or local libraries
4. **Run & Validate**: Execute simulations and verify successful completion
5. **Delegate Issues**: Hand off failures to `diagnosing-energy-models` skill
6. **Delegate Custom Measures**: Hand off measure creation to `writing-openstudio-model-measures` skill
7. **Reuse Tools**: Check `tools/` for existing reusable scripts before writing new one-off scripts

# OpenStudio CLI Basics

**Installation Path**: `C:\openstudio-3.10.0\bin\openstudio.exe`

**Core Commands**:
- `openstudio.exe run --workflow workflow.osw` - Run complete simulation
- `openstudio.exe run --measures_only --workflow workflow.osw` - Apply measures without simulation
- `openstudio.exe measure --update /path/to/measure/` - Update measure metadata

**File Conventions**:
- No whitespace in paths: use `underscored_path/my_model.osm` not `whitespace path/my model.osm`
- Use forward slashes in OSW files even on Windows

# Step-by-Step Workflow

## 1. Version the Current Model

Before any changes, create a versioned working directory using the setup tool:

```powershell
powershell -ExecutionPolicy Bypass -File .claude/skills/running-openstudio-models/tools/setup-model-version.ps1 `
  -SeedModel "path/to/model_v9.osm" -NewVersion "v10"
```

This creates the directory, copies the model and weather file, and generates `workflow.osw`. See the "Reusable Tools" section below for full details.

**Naming Convention**: `{projectname}_{YYYY-MM-DD}_v{X}.osm`
- `projectname`: Project identifier (e.g., Example-RecCenter, Example-Office)
- `YYYY-MM-DD`: Today's date
- `vX`: Version number for that day (v1, v2, v3, etc.)

## 2. Check for Weather File

Before running simulations, verify weather file exists:

```bash
# Check if .epw file exists in current directory
cmd /c "dir *.epw /b"
```

If no weather file found:
- **Prompt user**: "No weather file found. Please provide the `.epw` file for this project."
- **Ask for location**: User should place `.epw` in the project folder or provide path

## 3. Create or Modify OpenStudio Workflow (OSW)

Create a JSON workflow file to define the simulation:

**Basic OSW Template** (`workflow.osw`):
```json
{
  "seed_file": "Example-RecCenter_2025-12-03_v1.osm",
  "weather_file": "USA_CO_Fort_Collins.epw",
  "steps": []
}
```

**OSW with Measures**:
```json
{
  "seed_file": "Example-RecCenter_2025-12-03_v1.osm",
  "weather_file": "USA_CO_Fort_Collins.epw",
  "steps": [
    {
      "measure_dir_name": "AddMeter",
      "arguments": {
        "meter_name": "Electricity:Facility"
      }
    }
  ]
}
```

Use Node.js to generate OSW files programmatically:

```javascript
#!/usr/bin/env node
import { writeFile } from 'fs/promises';

const workflow = {
  seed_file: "Example-RecCenter_2025-12-03_v1.osm",
  weather_file: "USA_CO_Fort_Collins.epw",
  steps: []
};

await writeFile('workflow.osw', JSON.stringify(workflow, null, 2));
console.log("Created workflow.osw");
```

## 4. Search and Download Measures from BCL

The Building Component Library (BCL) hosts community measures.

**Search for measures**:
- Visit: https://bcl.nrel.gov/
- Search by keyword (e.g., "HVAC", "schedule", "envelope")
- Note the measure name and download URL

**Download measures manually**:
1. Download `.tar.gz` or `.zip` from BCL
2. Extract to `measures/` directory in project folder
3. Update measure metadata:

```bash
C:\openstudio-3.10.0\bin\openstudio.exe measure --update measures/measure_name/
```

**Organize measures**:
```bash
# Create measures directory
mkdir measures

# After downloading and extracting BCL measure
C:\openstudio-3.10.0\bin\openstudio.exe measure --update_all measures/
```

## 5. Apply Measures to Model

**Option A: Using OSW Workflow** (Recommended)

Add measures to the `steps` array in your OSW file:

```json
{
  "seed_file": "Example-RecCenter_2025-12-03_v1.osm",
  "weather_file": "USA_CO_Fort_Collins.epw",
  "steps": [
    {
      "measure_dir_name": "SetThermostatSchedules",
      "arguments": {
        "heating_setpoint": 20,
        "cooling_setpoint": 24
      }
    }
  ]
}
```

Run with measures only (no simulation):

```bash
C:\openstudio-3.10.0\bin\openstudio.exe run --measures_only --workflow workflow.osw
```

**Option B: Compute Measure Arguments**

If you need to see what arguments a measure accepts:

```bash
C:\openstudio-3.10.0\bin\openstudio.exe measure --compute_arguments Example-RecCenter_2025-12-03_v1.osm measures/SetThermostatSchedules/
```

## 6. Run Simulation

Execute the full simulation workflow:

```bash
C:\openstudio-3.10.0\bin\openstudio.exe run --workflow workflow.osw
```

**With debugging** (if issues expected):

```bash
C:\openstudio-3.10.0\bin\openstudio.exe --verbose run --debug --workflow workflow.osw
```

**Output Files**:
- `run/` directory created with simulation results
- `run/eplusout.err` - EnergyPlus error file
- `run/eplusout.sql` - Simulation results database
- `out.osw` - Workflow output with execution log

## 7. Check Simulation Success

**Quick Check**:
```bash
# Check if error file exists and is small (successful runs have minimal errors)
cmd /c "dir run\eplusout.err"

# View last 20 lines of error file
cmd /c "type run\eplusout.err | more +20"
```

**Success Indicators**:
- `out.osw` contains `"completed_status": "Success"`
- `eplusout.err` has no severe errors
- `eplusout.sql` file exists and has data

**Failure Indicators**:
- `out.osw` shows `"completed_status": "Fail"`
- `eplusout.err` contains `** Severe **` errors
- Missing output files

## 8. Handle Simulation Failures

If simulation fails, **delegate to `diagnosing-energy-models` skill**:

**Gather context**:
```bash
# Read error file
type run\eplusout.err

# Check out.osw for step_errors
type out.osw | findstr "step_errors"

# Get model summary
C:\openstudio-3.10.0\bin\openstudio.exe --verbose run --measures_only --workflow workflow.osw
```

**Hand off to diagnostic skill**:
- Provide path to `.err` file
- Include `out.osw` step_errors
- Describe what changes were made
- Share OSW file contents

Example delegation:
> "Simulation failed with severe errors. Delegating to `diagnosing-energy-models` skill to analyze `run/eplusout.err` and diagnose the issue. Changes made: [describe HVAC/zone/schedule modifications]."

# Model Modification Patterns

## Modifying HVAC Systems

OpenStudio models use object-oriented HVAC components. Common modifications:

**Access HVAC loops programmatically** (requires Ruby or Python bindings):
- Air loops: `model.getAirLoopHVACs`
- Plant loops: `model.getPlantLoops`
- Thermal zones: `model.getThermalZones`

**Recommended approach**: Use existing measures from BCL
- "Add HVAC System" measure family
- "Replace HVAC" measures
- "Modify HVAC" measures

If custom HVAC logic needed, **delegate to `writing-openstudio-model-measures` skill**.

## Modifying Thermal Zones

**Via Measures**:
- "Set Thermal Zone Properties"
- "Assign Spaces to Thermal Zones"
- "Merge Thermal Zones"

**Manual edits**: Not recommended via CLI (use OpenStudio Application GUI or custom measure)

## Modifying Schedules

**Via Measures**:
- "Set Thermostat Schedules"
- "Modify Occupancy Schedules"
- "Add Typical Schedules"

**Arguments example**:
```json
{
  "measure_dir_name": "SetThermostatSchedules",
  "arguments": {
    "heating_setpoint_schedule": "HtgSetp 20C",
    "cooling_setpoint_schedule": "ClgSetp 24C"
  }
}
```

## Modifying Constructions

**Via Measures**:
- "Set Construction Properties"
- "Increase Insulation R-Value"
- "Replace Constructions"

**Arguments example**:
```json
{
  "measure_dir_name": "IncreaseInsulationRValueForExteriorWalls",
  "arguments": {
    "r_value": 3.5
  }
}
```

# Validation Checklist

After running simulation, verify:
- [ ] New versioned `.osm` file created
- [ ] Simulation completed without severe errors
- [ ] `eplusout.sql` file generated
- [ ] `out.osw` shows `"completed_status": "Success"`
- [ ] Extract results with `extract-simulation-results.py` (see Reusable Tools)
- [ ] Compare against baseline with `compare-simulation-runs.py` if applicable
- [ ] Results make sense for changes made

If failures occur:
- [ ] Delegate to `diagnosing-energy-models` with error context
- [ ] Include `.err` file path and recent changes

# Parametric ECM Sweep Workflow

**When to recommend this workflow:**
- User wants to test multiple ECMs (controls, setpoints, schedules) against a baseline
- User wants to "run this overnight" or "test these 5 changes"
- User needs a side-by-side comparison table of EUI, unmet hours, and end-use breakdown
- Any time 3+ model variants need to be simulated and compared

**Why this exists:** Claude's context window + token cost makes it wasteful to babysit 6 sequential 20-minute simulations. This workflow front-loads the Claude work (writing ECM functions, validating text replacements) then runs unattended with zero tokens. Results are machine-readable markdown ready for the next session.

**How to use:**
1. Copy `tools/parametric-sweep-template.py` into the project step directory
2. Claude customizes the CONFIGURATION section and writes ECM functions
3. Phase 1 runs in-session: create variants + validate file sizes (catches bugs)
4. Phase 2 runs unattended: launch with `run_in_background: true` on the Bash tool
5. Phase 3 auto-generates `parametric_sweep_results/parametric_results.md`
6. User (or next session) reads the results markdown

**Key design patterns:**
- **Registry-driven**: ECMs are data (`ECM_REGISTRY` list), not hard-coded logic. Easy to add/remove.
- **Two-phase validation**: All variants are created and size-checked BEFORE any simulation runs. A corrupt OSM is caught in seconds, not after 30 min of sim time.
- **Text-based OSM editing**: `str.find()` + `str.replace()` with anchored searches. No OpenStudio SDK needed. Works on any OSM.
- **Sequential simulation**: E+ is RAM-heavy (~2-4 GB). Parallel runs cause swapping. Sequential is faster in practice.
- **Standalone**: Pure stdlib Python. No pip install, no venv, no Claude API. Copy and run.

# Reusable Tools

The `tools/` directory contains deterministic scripts for common simulation workflows. Always check here before writing one-off scripts.

## setup-model-version.ps1

**Purpose**: Creates a versioned working directory from a seed model, handling Windows paths with spaces.

**When to use**: At the start of every new model iteration (Step 1 of the workflow).

**Usage**:
```powershell
powershell -ExecutionPolicy Bypass -File .claude/skills/running-openstudio-models/tools/setup-model-version.ps1 `
  -SeedModel "C:\path\to\model_v9.osm" `
  -NewVersion "v10"
```

**What it does**:
- Creates `model_v10/` directory with `measures/` and `files/` subdirectories
- Copies seed model renamed to new version
- Auto-detects and copies `.epw` weather file from seed directory
- Generates empty `workflow.osw` pointing to new model and weather file
- Optionally copies measures from an existing measures directory (`-MeasuresSource`)

**Arguments**:
| Parameter | Required | Description |
|-----------|----------|-------------|
| `-SeedModel` | Yes | Path to the seed `.osm` file |
| `-NewVersion` | Yes | Version identifier (e.g., "v10", "v11") |
| `-ProjectDir` | No | Parent directory (defaults to seed model's directory) |
| `-WeatherFile` | No | Path to `.epw` file (auto-detected if not specified) |
| `-MeasuresSource` | No | Path to existing measures directory to copy |

## parametric-sweep-template.py

**Purpose**: Template for automated multi-ECM parametric analysis. Copy into project directory, customize configuration and ECM functions, launch unattended.

**When to use**: When testing 3+ ECM variants against a baseline. See "Parametric ECM Sweep Workflow" section above for full guidance.

**Usage**:
```bash
# 1. Copy template to project directory
cp .claude/skills/running-openstudio-models/tools/parametric-sweep-template.py \
   path/to/project/step/parametric_sweep.py

# 2. Edit CONFIGURATION section and ECM_REGISTRY (Claude does this)

# 3. Run (unattended — takes 10-30 min per ECM variant)
C:/Users/mcoalson/AppData/Local/Programs/Python/Python312/python.exe parametric_sweep.py
```

**What it does**:
- Phase 1: Creates versioned variant directories with modified OSM files, validates file sizes
- Phase 2: Runs sequential EnergyPlus simulations via OpenStudio CLI
- Phase 3: Extracts EUI, unmet hours, end-use breakdown from each `eplusout.sql`
- Generates `parametric_sweep_results/parametric_results.md` comparison table

**Customization points**:
| Section | What to change |
|---------|---------------|
| `PROJECT_DIR`, `BASE_OSM` | Paths to your baseline model |
| `BASELINE_EUI`, `BASELINE_END_USES` | Values from your baseline simulation |
| `TARGET_EUI` | Compliance target (0 to skip gap analysis) |
| `PROJECT_NAME` | Used in file naming and report headers |
| ECM functions | Write `ecm_*(osm_text) -> (text, info)` functions |
| `ECM_REGISTRY` | Register your ECM functions with version numbers |

**Python**: 3.x stdlib only (sqlite3, json, subprocess, shutil, uuid, logging, pathlib)

## extract-simulation-results.py

**Purpose**: Queries `eplusout.sql` and produces a structured results summary with facility unmet hours, zone-level unmet hours, end-use energy breakdown, site energy/EUI, and severe error count.

**When to use**: After every successful simulation run to extract and document results.

**Usage**:
```bash
python .claude/skills/running-openstudio-models/tools/extract-simulation-results.py <run-dir>
python .claude/skills/running-openstudio-models/tools/extract-simulation-results.py <run-dir> --format json --output results.json
python .claude/skills/running-openstudio-models/tools/extract-simulation-results.py <run-dir> --format both --output results
```

**What it does**:
- Locates `eplusout.sql` in `<run-dir>/` or `<run-dir>/run/`
- Queries facility-level occupied heating/cooling unmet hours
- Queries zone-by-zone occupied heating and cooling unmet hours (sorted by highest first)
- Extracts end-use energy breakdown by fuel type (GJ)
- Extracts site energy totals and converts EUI from MJ/m2 to kBtu/ft2
- Counts severe errors from `eplusout.err`

**Arguments**:
| Argument | Required | Description |
|----------|----------|-------------|
| `<run-dir>` | Yes | Path to run directory containing `eplusout.sql` |
| `--format` | No | Output format: `markdown` (default), `json`, or `both` |
| `--output` | No | Write to file instead of stdout |

**Python**: 3.x stdlib only (sqlite3, json, sys, os, re)

## compare-simulation-runs.py

**Purpose**: Side-by-side comparison of two EnergyPlus simulation runs with deltas and percentage changes.

**When to use**: After running a modified model to compare against its baseline.

**Usage**:
```bash
python .claude/skills/running-openstudio-models/tools/compare-simulation-runs.py <baseline-dir> <modified-dir>
python .claude/skills/running-openstudio-models/tools/compare-simulation-runs.py <baseline-dir> <modified-dir> --labels "v9" "v10"
python .claude/skills/running-openstudio-models/tools/compare-simulation-runs.py <baseline-dir> <modified-dir> --output comparison.md --json comparison.json
```

**What it does**:
- Queries both `eplusout.sql` databases
- Produces facility-level comparison table (unmet hours, fan energy, site energy, EUI, severe errors)
- Produces zone-by-zone occupied heating unmet hours delta table (sorted by baseline hours descending)
- Produces end-use energy comparison table with deltas and percentage changes
- Optionally outputs structured JSON for programmatic consumption

**Arguments**:
| Argument | Required | Description |
|----------|----------|-------------|
| `<baseline-dir>` | Yes | Path to baseline run directory |
| `<modified-dir>` | Yes | Path to modified run directory |
| `--labels A B` | No | Labels for the two runs (default: "Baseline" "Modified") |
| `--output FILE` | No | Write markdown to file instead of stdout |
| `--json FILE` | No | Write structured JSON comparison to file |
| `--threshold N` | No | Only show zones with >N unmet hours in either run (default: 0) |

**Python**: 3.x stdlib only (sqlite3, json, sys, os, re)

## Tool Notes

- **Python path**: Use `C:/Users/mcoalson/AppData/Local/Programs/Python/Python312/python.exe` (system `python` may point to wrong version)
- **EUI conversion**: Tools automatically convert EnergyPlus MJ/m2 to kBtu/ft2 (factor: 0.088055)
- **SQL location**: Tools check both `<dir>/eplusout.sql` and `<dir>/run/eplusout.sql`
- **Windows paths with spaces**: Use `setup-model-version.ps1` (PowerShell) rather than bash for directory setup when paths contain spaces

# Common Issues & Quick Fixes

## Issue: Missing Weather File

**Symptoms**: Workflow fails immediately with "weather file not found"

**Solution**:
```bash
# Check for weather files
cmd /c "dir *.epw /b"

# If missing, prompt user for .epw file path
```

**Prompt**: "No weather file found. Please provide the `.epw` file path for this project."

## Issue: Measure Not Found

**Symptoms**: `out.osw` shows measure directory not found

**Investigation**:
```bash
# Check measures directory
cmd /c "dir measures /b"

# Update measure if it exists
C:\openstudio-3.10.0\bin\openstudio.exe measure --update measures/MeasureName/
```

**Solution**:
- Download measure from BCL
- Verify measure directory name matches OSW `measure_dir_name`
- Run `--update` to regenerate metadata

## Issue: Model Translation Failure

**Symptoms**: Fails when translating OSM to IDF

**Delegate to**: `diagnosing-energy-models` skill
- Likely geometry issues (intersecting surfaces, non-planar surfaces)
- Could be orphaned objects

## Issue: EnergyPlus Simulation Severe Errors

**Symptoms**: Simulation runs but produces severe errors in `eplusout.err`

**Delegate to**: `diagnosing-energy-models` skill with:
- Path to `run/eplusout.err`
- Description of model changes
- OSW file contents

# Skill Orchestration

## When to Stay in This Skill
- Running existing models
- Applying downloaded/existing measures
- Making straightforward HVAC, zone, schedule, or construction changes
- Versioning and managing model files

## When to Delegate to `diagnosing-energy-models`
- Simulation fails with severe errors
- Model translation fails (OSM → IDF)
- Geometry errors appear
- Complex diagnostic analysis needed
- **Unmet hours root cause analysis** (per-zone diagnostics, multi-run comparison, heating load decomposition, capacity gap analysis — tools now live in `diagnosing-energy-models/tools/`)
- Provide: `.err` file path, `out.osw` errors, recent changes

## When to Delegate to `writing-openstudio-model-measures`
- Custom measure logic required
- Existing BCL measures don't fit use case
- Need to create reusable measure for repeated operations
- Provide: Desired functionality, model context, argument requirements

## Context Awareness

This skill integrates with work-command-center session tracking:

**Check Active Context:**

```bash
node .claude/skills/work-command-center/tools/session-state.js status
```

Returns: Project name, project number, duration, and deliverables context

**Log Activity Checkpoints:**

```bash
node .claude/skills/work-command-center/tools/session-state.js checkpoint \
  --activity "running-openstudio-models: Simulation completed successfully, annual EUI: 42.3 kBtu/sf"
```

**Signal Completion (called by WCC after skill returns):**

```bash
node .claude/skills/work-command-center/tools/session-state.js skill-complete \
  --skill-name "running-openstudio-models" \
  --summary "Simulation successful. EUI: 42.3 kBtu/sf. Model saved as v2.osm." \
  --outcome "success"
```

**Benefits:**

- WCC tracks time spent in this skill
- Session logs include skill work breakdown
- Context visible across skill transitions
- Deliverables auto-update from skill outcomes

# Reference Resources

## Official Documentation
- **OpenStudio CLI Reference**: https://nrel.github.io/OpenStudio-user-documentation/reference/command_line_interface/
- **OpenStudio SDK Docs**: https://nrel.github.io/OpenStudio-user-documentation/
- **Measure Writer's Guide**: https://nrel.github.io/OpenStudio-user-documentation/reference/measure_writing_guide/

## Troubleshooting Resources
- **OpenStudio Coalition Troubleshooting**: https://openstudiocoalition.org/getting_started/troubleshooting/
- **Unmet Hours Forum**: https://unmethours.com/ (community Q&A)

## Measure Resources
- **Building Component Library (BCL)**: https://bcl.nrel.gov/
- **NREL GitHub**: https://github.com/NREL/ (official measures and tools)

See `./openstudio-cli-reference.md` for detailed CLI command syntax and examples.


## Saving Next Steps

When running-openstudio-models work is complete or paused:

```bash
node .claude/skills/work-command-center/tools/add-skill-next-steps.js \
  --skill "running-openstudio-models" \
  --content "## Priority Tasks
1. Run simulation with updated HVAC measures
2. Validate results and check for severe errors
3. Extract EUI and utility costs from eplusout.sql"
```

See: `.claude/skills/work-command-center/skill-next-steps-convention.md`
