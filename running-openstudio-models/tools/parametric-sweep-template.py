"""
Parametric ECM Sweep — Template
================================

Automated parametric analysis of Energy Conservation Measures (ECMs).
Creates versioned model variants from a baseline OSM, runs EnergyPlus
simulations via OpenStudio CLI, and compiles results into a comparison
markdown report.

This is a TEMPLATE. Copy it into your project directory and customize
the CONFIGURATION section and ECM_REGISTRY with project-specific values.

Architecture:
    Phase 1 — Create variant OSM files via text replacement, validate sizes
    Phase 2 — Run EnergyPlus simulations sequentially via OpenStudio CLI
    Phase 3 — Extract results from eplusout.sql, generate comparison report

Key design decisions:
    - Pure stdlib (no pip install needed)
    - Text-based OSM manipulation (no OpenStudio SDK dependency)
    - Two-phase validation catches corrupt files before wasting sim time
    - Sequential sims (not parallel) to avoid RAM contention on E+ runs
    - Runs unattended — launch and walk away, read results when done

Usage:
    1. Copy this file into your project step directory
    2. Update CONFIGURATION section with your paths and baseline values
    3. Write ECM functions that take osm_text and return (modified_text, info)
    4. Register ECMs in ECM_REGISTRY
    5. Run:
       C:/Users/mcoalson/AppData/Local/Programs/Python/Python312/python.exe parametric_sweep.py

Runtime: ~10-30 minutes per simulation depending on model complexity.

Origin: generalized from a parametric sweep implementation.
Generalized from project-specific script into reusable template.
"""

import os
import sys
import shutil
import subprocess
import sqlite3
import json
import time
import uuid
import logging
from pathlib import Path
from datetime import datetime


# ============================================================================
# CONFIGURATION — CUSTOMIZE THESE FOR YOUR PROJECT
# ============================================================================

# Python path (explicit to avoid wrong-version issues on this machine)
PYTHON_EXE = Path("C:/Users/mcoalson/AppData/Local/Programs/Python/Python312/python.exe")

# OpenStudio installation
OPENSTUDIO_EXE = Path("C:/openstudio-3.10.0/bin/openstudio.exe")

# Project paths — UPDATE THESE
PROJECT_DIR = Path(".")  # Set to your project step directory
MODEL_DIR = PROJECT_DIR / "model"
BASE_RUN_DIR = MODEL_DIR / "YOUR_BASELINE_DIR"           # Directory containing base OSM
BASE_OSM = BASE_RUN_DIR / "YOUR_BASELINE.osm"            # Base OSM filename

# Weather file
WEATHER_FILE_NAME = "YOUR_WEATHER_FILE.epw"
WEATHER_FILE_SRC = BASE_RUN_DIR / WEATHER_FILE_NAME

# Results output
RESULTS_DIR = PROJECT_DIR / "parametric_sweep_results"

# Baseline reference values — UPDATE FROM YOUR BASELINE SIMULATION
BASELINE_EUI = 0.0          # kBtu/ft2 from baseline sim
BASELINE_HEATING_UNMET = 0  # hours
BASELINE_COOLING_UNMET = 0  # hours
BASELINE_TOTAL_GJ = 0.0     # GJ

# Baseline end-use breakdown (GJ) — UPDATE FROM YOUR BASELINE SIMULATION
# Used for the comparison table. Get these from extract-simulation-results.py.
BASELINE_END_USES = {
    "Heating": 0,
    "Cooling": 0,
    "Interior Lighting": 0,
    "Exterior Lighting": 0,
    "Interior Equipment": 0,
    "Exterior Equipment": 0,
    "Fans": 0,
    "Pumps": 0,
    "Heat Rejection": 0,
    "Water Systems": 0,
}

# Target EUI for gap analysis (set to 0 to skip gap analysis)
TARGET_EUI = 0.0  # kBtu/ft2

# Project name for reporting (used in markdown headers and model naming)
PROJECT_NAME = "YOUR_PROJECT"

# Version numbering — first ECM gets this version number, increments from here
FIRST_ECM_VERSION = 2  # e.g., if baseline is v1, first ECM is v2

# Conversion factor (don't change)
MJ_M2_TO_KBTU_FT2 = 0.088055

# Simulation timeout per run (seconds)
SIM_TIMEOUT = 3600  # 1 hour


# ============================================================================
# LOGGING
# ============================================================================

def setup_logging():
    """Configure logging to both file and console."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = RESULTS_DIR / f"sweep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


# ============================================================================
# ECM MODIFICATION FUNCTIONS
#
# Each function takes the full OSM text (string) and returns:
#   (modified_text, {"changes": [list of human-readable change descriptions]})
#
# Functions MUST raise ValueError if expected patterns are not found.
# This catches bugs before simulation time is wasted.
#
# TIPS FOR WRITING ECM FUNCTIONS:
#
# 1. Use exact string matching — copy the line from the OSM verbatim,
#    including leading spaces and the !- comment. This is more reliable
#    than regex for OSM files.
#
# 2. Validate occurrence count — always check how many times a pattern
#    appears and assert the expected count. Catches model structure changes.
#
# 3. For targeted replacements (changing one instance among many), use
#    find() with start/end bounds relative to a unique anchor string.
#    IMPORTANT: Python's find(sub, start, end) requires the ENTIRE
#    substring to fit within [start, end). If your pattern is 68 chars
#    and starts at offset 480, you need end >= 548, not just > 480.
#
# 4. For adding new OSM objects (schedules, equipment), append to the
#    end of the file. Generate UUIDs with uuid.uuid4() for handles.
#    All OS objects need a handle in {uuid} format as their first field.
#
# 5. Return human-readable change descriptions — these go into the log
#    and help verify the right changes were made.
# ============================================================================


def ecm_example_field_change(osm_text):
    """EXAMPLE: Simple field value replacement.

    Replace one specific field value. Good for:
    - Changing setpoints, COPs, efficiencies
    - Toggling boolean fields (Yes/No)
    - Changing control types (Load/Setpoint)

    Delete this function and write your own.
    """
    old = "  VALUE_TO_FIND,                          !- Field Name"
    new = "  NEW_VALUE,                              !- Field Name"

    count = osm_text.count(old)
    if count != 1:  # Adjust expected count for your model
        raise ValueError(f"Expected 1 instance, found {count}")

    result = osm_text.replace(old, new)
    return result, {"changes": ["Object Name: Field -> NEW_VALUE"]}


def ecm_example_targeted_replace(osm_text):
    """EXAMPLE: Replace a field value near a specific anchor object.

    When the same field pattern appears on multiple objects, use an
    anchor string (object name) to target the right one.

    Delete this function and write your own.
    """
    changes = []
    result = osm_text

    # List of (anchor_name, search_range) pairs
    targets = [
        ("Unique Object Name 1", 600),
        ("Unique Object Name 2", 600),
    ]

    old_value = "  OldValue,                               !- Some Field"
    new_value = "  NewValue,                               !- Some Field"

    for anchor, search_range in targets:
        anchor_idx = result.find(anchor)
        if anchor_idx == -1:
            raise ValueError(f"Could not find anchor: {anchor}")

        # find() within bounded range around the anchor
        val_idx = result.find(old_value, anchor_idx, anchor_idx + search_range)
        if val_idx == -1:
            raise ValueError(f"Could not find field near '{anchor}'")

        result = (
            result[:val_idx] + new_value + result[val_idx + len(old_value):]
        )
        changes.append(f"{anchor}: Some Field -> NewValue")

    return result, {"changes": changes}


def ecm_example_add_schedule(osm_text):
    """EXAMPLE: Add new OSM objects (schedule, equipment, etc.)

    When an ECM needs to CREATE objects that don't exist in the model,
    generate UUID handles and append the objects to the OSM text.

    Delete this function and write your own.
    """
    changes = []

    # Generate UUIDs for each new object
    h_schedule = str(uuid.uuid4())
    h_day_sched = str(uuid.uuid4())
    h_type_lim = str(uuid.uuid4())

    new_handle = "{" + h_schedule + "}"

    # Build OS objects to append (use f-strings with doubled braces for literal {})
    new_objects = f"""

OS:ScheduleTypeLimits,
  {{{h_type_lim}}},                        !- Handle
  My Schedule Type,                       !- Name
  0,                                      !- Lower Limit Value
  1,                                      !- Upper Limit Value
  Discrete,                               !- Numeric Type
  Availability;                           !- Unit Type

OS:Schedule:Day,
  {{{h_day_sched}}},                       !- Handle
  My Day Schedule,                        !- Name
  {{{h_type_lim}}},                        !- Schedule Type Limits Name
  No,                                     !- Interpolate to Timestep
  24,                                     !- Hour 1
  0,                                      !- Minute 1
  1;                                      !- Value Until Time 1
"""

    result = osm_text + new_objects
    changes.append("Created My Day Schedule (always on)")

    # Now replace references to old handle with new handle on target objects
    # (similar to targeted replace pattern above)

    return result, {"changes": changes}


def ecm_combined(osm_text):
    """Apply all registered ECMs in sequence.

    Automatically chains all non-combined ECM functions from the registry.
    This should always be the LAST entry in ECM_REGISTRY.
    """
    all_changes = []
    for ecm in ECM_REGISTRY:
        if ecm["name"] == "combined_all_ecms":
            continue  # Skip self
        osm_text, info = ecm["func"](osm_text)
        all_changes.extend(info["changes"])
    return osm_text, {"changes": all_changes}


# ============================================================================
# ECM REGISTRY
#
# Each entry needs:
#   version  — integer version number for the variant (v14, v15, etc.)
#   name     — short snake_case name (used in directory and file names)
#   description — human-readable description (used in reports)
#   func     — reference to the ECM modification function
#
# Order matters: combined_all_ecms should be LAST.
# Version numbers should be sequential from FIRST_ECM_VERSION.
# ============================================================================

ECM_REGISTRY = [
    # --- Replace these with your actual ECMs ---
    # {
    #     "version": FIRST_ECM_VERSION,
    #     "name": "my_first_ecm",
    #     "description": "Description of what this ECM changes",
    #     "func": ecm_example_field_change,
    # },
    # {
    #     "version": FIRST_ECM_VERSION + 1,
    #     "name": "my_second_ecm",
    #     "description": "Description of what this ECM changes",
    #     "func": ecm_example_targeted_replace,
    # },
    # --- Combined should always be last ---
    # {
    #     "version": FIRST_ECM_VERSION + N,
    #     "name": "combined_all_ecms",
    #     "description": "All ECMs applied together",
    #     "func": ecm_combined,
    # },
]


# ============================================================================
# ORCHESTRATOR FUNCTIONS — These generally don't need modification
# ============================================================================

def create_variant(ecm_config, base_osm_text, model_dir, weather_src):
    """Create a variant model directory with modified OSM.

    Phase 1 validation: file size must be within 15% of original.
    Returns (run_dir, model_name) on success, raises on failure.
    """
    version = ecm_config["version"]
    name = ecm_config["name"]
    model_name = f"{PROJECT_NAME}_v{version}_{name}"
    run_dir = model_dir / model_name

    logging.info(f"  Creating variant: {model_name}")

    # Create directory structure
    run_dir.mkdir(parents=True, exist_ok=True)
    files_dir = run_dir / "files"
    files_dir.mkdir(exist_ok=True)

    # Apply ECM modification
    ecm_func = ecm_config["func"]
    modified_osm, info = ecm_func(base_osm_text)

    for change in info["changes"]:
        logging.info(f"    -> {change}")

    # Validate: file size within 15% of original
    original_size = len(base_osm_text)
    modified_size = len(modified_osm)
    size_ratio = modified_size / original_size
    if size_ratio < 0.85 or size_ratio > 1.15:
        raise ValueError(
            f"Modified OSM size {modified_size:,} differs >15% from "
            f"original {original_size:,} (ratio: {size_ratio:.3f})"
        )

    # Write modified OSM
    osm_path = run_dir / f"{model_name}.osm"
    with open(osm_path, "w", encoding="utf-8") as f:
        f.write(modified_osm)

    # Copy weather file to both locations (run dir and files/)
    shutil.copy2(weather_src, run_dir / weather_src.name)
    shutil.copy2(weather_src, files_dir / weather_src.name)

    # Create workflow.osw
    workflow = {
        "seed_file": f"{model_name}.osm",
        "weather_file": weather_src.name,
        "steps": [],
    }
    with open(run_dir / "workflow.osw", "w") as f:
        json.dump(workflow, f, indent=2)

    logging.info(
        f"    OSM: {modified_size:,} bytes ({size_ratio:.3f}x original)"
    )

    return run_dir, model_name


def run_simulation(run_dir, model_name):
    """Run EnergyPlus simulation via OpenStudio CLI.

    Returns True on success, False on failure.
    """
    workflow_path = run_dir / "workflow.osw"

    logging.info(f"  Running simulation: {model_name}")
    logging.info(f"    Working dir: {run_dir}")
    start_time = time.time()

    try:
        result = subprocess.run(
            [str(OPENSTUDIO_EXE), "run", "--workflow", str(workflow_path)],
            cwd=str(run_dir),
            capture_output=True,
            text=True,
            timeout=SIM_TIMEOUT,
        )

        elapsed = time.time() - start_time
        logging.info(
            f"    Completed in {elapsed / 60:.1f} minutes "
            f"(exit code: {result.returncode})"
        )

        if result.returncode != 0:
            logging.error(f"    STDERR (last 500 chars): {result.stderr[-500:]}")
            return False

        # Check for fatal errors in .err file
        err_file = run_dir / "run" / "eplusout.err"
        if err_file.exists():
            err_text = err_file.read_text()
            severe_count = err_text.count("** Severe  **")
            fatal_count = err_text.count("**  Fatal  **")
            warning_count = err_text.count("** Warning **")
            logging.info(
                f"    E+ errors: {severe_count} severe, {fatal_count} fatal, "
                f"{warning_count} warnings"
            )
            if fatal_count > 0:
                logging.error("    FATAL errors detected — results may be invalid")
                return False

        return True

    except subprocess.TimeoutExpired:
        logging.error(f"    Simulation timed out after {SIM_TIMEOUT // 60} minutes!")
        return False
    except Exception as e:
        logging.error(f"    Simulation error: {e}")
        return False


def parse_results(run_dir, model_name):
    """Extract key metrics from eplusout.sql.

    Returns dict with: model, eui, heating_unmet, cooling_unmet,
    total_energy_gj, end_uses, end_use_totals, building_area_m2
    """
    sql_path = run_dir / "run" / "eplusout.sql"
    if not sql_path.exists():
        logging.error(f"    eplusout.sql not found in {run_dir / 'run'}")
        return None

    try:
        conn = sqlite3.connect(str(sql_path))
        cursor = conn.cursor()

        results = {"model": model_name}

        # Site EUI (stored as MJ/m2 in E+ SQL)
        cursor.execute(
            "SELECT Value FROM TabularDataWithStrings "
            "WHERE TableName='Site and Source Energy' "
            "AND RowName='Total Site Energy' "
            "AND ColumnName='Energy Per Total Building Area' "
            "AND Units='MJ/m2'"
        )
        row = cursor.fetchone()
        if row:
            eui_mj_m2 = float(row[0].strip())
            results["eui"] = round(eui_mj_m2 * MJ_M2_TO_KBTU_FT2, 1)
        else:
            results["eui"] = None

        # Heating unmet hours (occupied)
        cursor.execute(
            "SELECT Value FROM TabularDataWithStrings "
            "WHERE RowName='Time Setpoint Not Met During Occupied Heating' "
            "AND Units='Hours'"
        )
        row = cursor.fetchone()
        results["heating_unmet"] = round(float(row[0].strip()), 1) if row else None

        # Cooling unmet hours (occupied)
        cursor.execute(
            "SELECT Value FROM TabularDataWithStrings "
            "WHERE RowName='Time Setpoint Not Met During Occupied Cooling' "
            "AND Units='Hours'"
        )
        row = cursor.fetchone()
        results["cooling_unmet"] = round(float(row[0].strip()), 1) if row else None

        # Total site energy (GJ)
        cursor.execute(
            "SELECT Value FROM TabularDataWithStrings "
            "WHERE TableName='Site and Source Energy' "
            "AND RowName='Total Site Energy' "
            "AND ColumnName='Total Energy' "
            "AND Units='GJ'"
        )
        row = cursor.fetchone()
        results["total_energy_gj"] = round(float(row[0].strip()), 1) if row else None

        # Building area (m2)
        cursor.execute(
            "SELECT Value FROM TabularDataWithStrings "
            "WHERE TableName='Building Area' "
            "AND RowName='Total Building Area' "
            "AND Units='m2'"
        )
        row = cursor.fetchone()
        results["building_area_m2"] = round(float(row[0].strip()), 1) if row else None

        # End use breakdown (GJ) — aggregate across fuel types
        end_uses = {}
        cursor.execute(
            "SELECT RowName, ColumnName, Value FROM TabularDataWithStrings "
            "WHERE TableName='End Uses' AND Units='GJ' "
            "AND RowName != '' AND RowName != 'Total End Uses'"
        )
        for row_name, col_name, value in cursor.fetchall():
            val = value.strip()
            if val:
                row_name = row_name.strip()
                if row_name not in end_uses:
                    end_uses[row_name] = {}
                end_uses[row_name][col_name.strip()] = round(float(val), 1)
        results["end_uses"] = end_uses

        # Calculate total by end-use category
        end_use_totals = {}
        for category, fuels in end_uses.items():
            end_use_totals[category] = round(sum(fuels.values()), 1)
        results["end_use_totals"] = end_use_totals

        conn.close()
        return results

    except Exception as e:
        logging.error(f"    SQL parse error: {e}")
        return None


# ============================================================================
# RESULTS REPORTING
# ============================================================================

def write_results_markdown(all_results, output_dir):
    """Write results comparison table to markdown file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "parametric_results.md"

    lines = [
        f"# {PROJECT_NAME} — Parametric ECM Sweep Results",
        "",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Base model**: {BASE_OSM.name}",
    ]
    if TARGET_EUI > 0:
        lines.append(f"**Target EUI**: {TARGET_EUI} kBtu/ft2")
    lines.extend([
        "",
        "## Results Summary",
        "",
        "| Run | EUI (kBtu/ft2) | Delta EUI | Delta % | Htg Unmet (hr) | Clg Unmet (hr) | Total (GJ) | Status |",
        "|-----|----------------|-----------|---------|----------------|----------------|------------|--------|",
    ])

    baseline_eui = BASELINE_EUI

    for r in all_results:
        eui = r.get("eui")
        htg = r.get("heating_unmet")
        clg = r.get("cooling_unmet")
        total_gj = r.get("total_energy_gj")

        if eui is not None and baseline_eui > 0:
            delta_eui = eui - baseline_eui
            delta_pct = (delta_eui / baseline_eui) * 100
            eui_str = f"{eui:.1f}"
            delta_str = f"{delta_eui:+.1f}"
            pct_str = f"{delta_pct:+.1f}%"
        elif eui is not None:
            eui_str = f"{eui:.1f}"
            delta_str = "---"
            pct_str = "---"
        else:
            eui_str = "FAILED"
            delta_str = "---"
            pct_str = "---"

        htg_str = f"{htg:.0f}" if htg is not None else "---"
        clg_str = f"{clg:.0f}" if clg is not None else "---"
        gj_str = f"{total_gj:.0f}" if total_gj is not None else "---"
        status = r.get("error", "OK")

        name = r.get("model", "???")
        # Shorten model name for table readability
        short_name = name.replace(f"{PROJECT_NAME}_", "")

        lines.append(
            f"| {short_name} | {eui_str} | {delta_str} | {pct_str} | "
            f"{htg_str} | {clg_str} | {gj_str} | {status} |"
        )

    # Gap analysis (if target provided)
    if TARGET_EUI > 0 and baseline_eui > 0:
        lines.extend([
            "",
            "## Gap Analysis",
            "",
            f"- **Current EUI**: {baseline_eui:.1f} kBtu/ft2",
            f"- **Target EUI**: {TARGET_EUI} kBtu/ft2",
            f"- **Gap**: {baseline_eui - TARGET_EUI:.1f} kBtu/ft2 "
            f"({((baseline_eui - TARGET_EUI) / baseline_eui) * 100:.1f}% reduction needed)",
        ])

        # Best result
        valid = [r for r in all_results
                 if r.get("eui") is not None and "Baseline" not in r.get("model", "")]
        if valid:
            best = min(valid, key=lambda r: r["eui"])
            lines.extend([
                "",
                f"- **Best ECM result**: {best['model']} at {best['eui']:.1f} kBtu/ft2",
                f"- **Remaining gap to target**: {best['eui'] - TARGET_EUI:.1f} kBtu/ft2",
            ])

    # End use comparison
    lines.extend(["", "## End Use Comparison (GJ)", ""])

    results_with_enduses = [r for r in all_results if r.get("end_use_totals")]
    if results_with_enduses:
        key_categories = [
            "Heating", "Cooling", "Interior Lighting", "Exterior Lighting",
            "Interior Equipment", "Exterior Equipment", "Fans", "Pumps",
            "Heat Rejection", "Water Systems",
        ]

        header = "| End Use |"
        sep = "|---------|"
        for r in results_with_enduses:
            short = r["model"].replace(f"{PROJECT_NAME}_", "")[:20]
            header += f" {short} |"
            sep += "------|"
        lines.append(header)
        lines.append(sep)

        for cat in key_categories:
            has_data = any(r["end_use_totals"].get(cat, 0) > 0 for r in results_with_enduses)
            if not has_data:
                continue
            row = f"| {cat} |"
            for r in results_with_enduses:
                val = r["end_use_totals"].get(cat, 0)
                row += f" {val:.0f} |"
            lines.append(row)
    else:
        lines.append("*End use data not available*")

    # ECM descriptions
    lines.extend(["", "## ECM Descriptions", ""])
    for ecm in ECM_REGISTRY:
        lines.append(f"- **v{ecm['version']} {ecm['name']}**: {ecm['description']}")

    lines.extend([
        "",
        "---",
        f"*Sweep completed {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logging.info(f"  Results written to: {output_path}")
    return output_path


# ============================================================================
# MAIN
# ============================================================================

def main():
    log_file = setup_logging()

    logging.info("=" * 70)
    logging.info(f"{PROJECT_NAME} — Parametric ECM Sweep")
    logging.info("=" * 70)
    logging.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Base model: {BASE_OSM}")
    logging.info(f"Weather: {WEATHER_FILE_NAME}")
    logging.info(f"OpenStudio: {OPENSTUDIO_EXE}")
    logging.info(f"ECMs to test: {len(ECM_REGISTRY)}")
    logging.info(f"Log file: {log_file}")
    logging.info("")

    # --- Validate prerequisites ---
    errors = []
    if not BASE_OSM.exists():
        errors.append(f"Base OSM not found: {BASE_OSM}")
    if not WEATHER_FILE_SRC.exists():
        errors.append(f"Weather file not found: {WEATHER_FILE_SRC}")
    if not OPENSTUDIO_EXE.exists():
        errors.append(f"OpenStudio not found: {OPENSTUDIO_EXE}")
    if len(ECM_REGISTRY) == 0:
        errors.append("ECM_REGISTRY is empty — add your ECMs before running")

    if errors:
        for e in errors:
            logging.error(e)
        return 1

    # --- Read base OSM ---
    logging.info("Reading base OSM...")
    with open(BASE_OSM, "r", encoding="utf-8") as f:
        base_osm_text = f.read()
    line_count = base_osm_text.count("\n")
    logging.info(f"  {len(base_osm_text):,} bytes, {line_count:,} lines")

    # =========================================================================
    # PHASE 1: Create ECM Variants (validate before simulating)
    # =========================================================================
    logging.info("")
    logging.info("=" * 70)
    logging.info("PHASE 1: Creating ECM Variants")
    logging.info("=" * 70)

    variants = []
    for ecm in ECM_REGISTRY:
        logging.info("")
        logging.info(f"[v{ecm['version']}] {ecm['description']}")
        try:
            run_dir, model_name = create_variant(
                ecm, base_osm_text, MODEL_DIR, WEATHER_FILE_SRC
            )
            variants.append({
                "ecm": ecm,
                "run_dir": run_dir,
                "model_name": model_name,
                "status": "created",
            })
        except Exception as e:
            model_name = f"{PROJECT_NAME}_v{ecm['version']}_{ecm['name']}"
            logging.error(f"  FAILED: {e}")
            variants.append({
                "ecm": ecm,
                "run_dir": None,
                "model_name": model_name,
                "status": "creation_failed",
                "error": str(e),
            })

    created = sum(1 for v in variants if v["status"] == "created")
    failed = len(variants) - created
    logging.info("")
    logging.info(f"Phase 1 complete: {created}/{len(variants)} variants created")
    if failed > 0:
        logging.warning(f"  {failed} variant(s) failed creation — fix ECM functions before proceeding")

    if created == 0:
        logging.error("No variants created successfully. Aborting.")
        return 1

    # =========================================================================
    # PHASE 2: Run Simulations (sequential to avoid RAM contention)
    # =========================================================================
    logging.info("")
    logging.info("=" * 70)
    logging.info("PHASE 2: Running Simulations")
    logging.info(f"  Estimated time: {created * 20:.0f} minutes ({created} sims x ~20 min)")
    logging.info("=" * 70)

    phase2_start = time.time()

    for i, variant in enumerate(variants):
        if variant["status"] != "created":
            logging.info(
                f"\n  [{i+1}/{len(variants)}] SKIPPING {variant['model_name']} "
                f"(creation failed)"
            )
            continue

        logging.info(f"\n  [{i+1}/{len(variants)}] {variant['model_name']}")
        logging.info(f"    {variant['ecm']['description']}")

        success = run_simulation(variant["run_dir"], variant["model_name"])
        variant["status"] = "simulated" if success else "simulation_failed"

    phase2_elapsed = time.time() - phase2_start
    simulated = sum(1 for v in variants if v["status"] == "simulated")
    logging.info("")
    logging.info(
        f"Phase 2 complete: {simulated}/{created} simulations successful "
        f"({phase2_elapsed / 60:.1f} minutes total)"
    )

    # =========================================================================
    # PHASE 3: Collect Results
    # =========================================================================
    logging.info("")
    logging.info("=" * 70)
    logging.info("PHASE 3: Collecting Results")
    logging.info("=" * 70)

    all_results = []

    # Add baseline from stored values
    if BASELINE_EUI > 0:
        baseline_result = {
            "model": "Baseline",
            "eui": BASELINE_EUI,
            "heating_unmet": BASELINE_HEATING_UNMET,
            "cooling_unmet": BASELINE_COOLING_UNMET,
            "total_energy_gj": BASELINE_TOTAL_GJ,
            "end_use_totals": BASELINE_END_USES,
        }
        all_results.append(baseline_result)

    # Parse ECM results
    for variant in variants:
        if variant["status"] == "simulated":
            logging.info(f"  Parsing: {variant['model_name']}")
            results = parse_results(variant["run_dir"], variant["model_name"])
            if results:
                all_results.append(results)
                logging.info(
                    f"    EUI={results.get('eui', 'N/A')}, "
                    f"Htg={results.get('heating_unmet', 'N/A')}, "
                    f"Clg={results.get('cooling_unmet', 'N/A')}"
                )
            else:
                all_results.append({
                    "model": variant["model_name"],
                    "eui": None,
                    "error": "SQL parse failed",
                })
        else:
            all_results.append({
                "model": variant["model_name"],
                "eui": None,
                "error": variant.get("error", variant["status"]),
            })

    # Write results markdown
    results_path = write_results_markdown(all_results, RESULTS_DIR)

    # Print summary to console
    logging.info("")
    logging.info("=" * 70)
    logging.info("PARAMETRIC SWEEP COMPLETE")
    logging.info("=" * 70)
    logging.info(f"Results: {results_path}")
    logging.info(f"Log: {log_file}")
    logging.info("")

    # Quick summary table
    logging.info(f"{'Model':<35} {'EUI':>10} {'Delta':>8} {'Htg':>6} {'Clg':>6}")
    logging.info("-" * 70)
    for r in all_results:
        name = r.get("model", "???").replace(f"{PROJECT_NAME}_", "")[:35]
        eui = r.get("eui")
        htg = r.get("heating_unmet")
        clg = r.get("cooling_unmet")

        eui_str = f"{eui:.1f}" if eui else "FAIL"
        delta_str = f"{eui - BASELINE_EUI:+.1f}" if (eui and BASELINE_EUI > 0) else "---"
        htg_str = f"{htg:.0f}" if htg is not None else "---"
        clg_str = f"{clg:.0f}" if clg is not None else "---"

        logging.info(f"{name:<35} {eui_str:>10} {delta_str:>8} {htg_str:>6} {clg_str:>6}")

    if TARGET_EUI > 0:
        logging.info("")
        logging.info(
            f"Target: {TARGET_EUI} kBtu/ft2 | "
            f"Gap from baseline: {BASELINE_EUI - TARGET_EUI:.1f} kBtu/ft2"
        )
    logging.info("Done.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
