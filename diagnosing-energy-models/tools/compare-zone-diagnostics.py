"""
compare-zone-diagnostics.py — Multi-run EnergyPlus diagnostic comparison tool

Runs the same analysis as diagnose-unmet-hours.py across any number of EnergyPlus
iterations and produces side-by-side comparison tables showing how each zone's
diagnostic metrics changed between runs. This is the trajectory analysis — did coil
capacity change? Did unmet hours correlate with envelope exposure? Which zones
improved and which regressed?

Usage:
    python compare-zone-diagnostics.py run1/ run2/
    python compare-zone-diagnostics.py run1/ run2/ run3/ --labels "Baseline" "Upsized" "Final"
    python compare-zone-diagnostics.py run1/ run2/ --format markdown
    python compare-zone-diagnostics.py run1/ run2/ --format json --json results.json
    python compare-zone-diagnostics.py run1/ run2/ --format csv --output results.csv
    python compare-zone-diagnostics.py run1/ run2/ run3/ --threshold 50

Arguments:
    run-dir-1 run-dir-2 [run-dir-3 ...]
                    Two or more paths to EnergyPlus run directories, each
                    containing eplusout.sql. Accepts the run/ subdirectory itself
                    or the parent workflow dir (auto-detects run/eplusout.sql).

Options:
    --labels LABEL [LABEL ...]
                    Human-readable labels for each run directory (must match
                    the number of run directories). Defaults to "Run 1", "Run 2", etc.
    --threshold N   Only show zones with > N occupied unmet hours in ANY run
                    (default: 0)
    --output FILE   Write output to file instead of stdout
    --json FILE     Write JSON output to file (independent of --format)
    --format FORMAT Output format: markdown (default), json, csv

Output:
    Multi-run comparison table with columns for each run's metrics and deltas
    between consecutive runs. Includes:
    1. Facility summary (total htg/clg unmet per run)
    2. Zone comparison table (sorted by worst unmet in final run)
    3. Delta summary (most improved and most regressed zones)

Python: 3.12 (stdlib only - sqlite3, json, csv, sys, os, argparse, io, math)
"""

import sqlite3
import json
import csv
import sys
import os
import argparse
import math
import io


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def find_sql(run_dir):
    """Locate eplusout.sql, checking run_dir and run_dir/run/."""
    for candidate in [
        os.path.join(run_dir, 'eplusout.sql'),
        os.path.join(run_dir, 'run', 'eplusout.sql'),
    ]:
        if os.path.exists(candidate):
            return candidate
    print(f"ERROR: Cannot find eplusout.sql in {run_dir}", file=sys.stderr)
    sys.exit(1)


def safe_float(value, default=0.0):
    """Convert a SQL value to float, returning default if conversion fails."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_div(numerator, denominator, default=None):
    """Safe division returning default when numerator/denominator is None or zero."""
    if numerator is None or denominator is None or denominator == 0:
        return default
    return numerator / denominator


def fmt_val(value, precision=1, suffix=''):
    """Format a numeric value for display, returning 'N/A' for None."""
    if value is None:
        return 'N/A'
    return f"{value:.{precision}f}{suffix}"


def fmt_delta(value, precision=1):
    """Format a delta value with +/- sign, returning 'N/A' for None."""
    if value is None:
        return 'N/A'
    sign = '+' if value > 0 else ''
    return f"{sign}{value:.{precision}f}"


def fmt_pct(value, precision=1):
    """Format a percentage delta, returning 'N/A' for None."""
    if value is None:
        return 'N/A'
    sign = '+' if value > 0 else ''
    return f"{sign}{value:.{precision}f}%"


# ---------------------------------------------------------------------------
# SQL query functions — each extracts one category of data from eplusout.sql
# ---------------------------------------------------------------------------

def query_zone_unmet(conn):
    """Zone-by-zone unmet hours from SystemSummary -> Time Setpoint Not Met."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'SystemSummary'
        AND TableName = 'Time Setpoint Not Met'
        AND ColumnName IN ('During Occupied Heating', 'During Occupied Cooling',
                           'During Heating', 'During Cooling')
    """)
    zones = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name == 'FACILITY':
            continue
        if name not in zones:
            zones[name] = {}
        zones[name][col_name.strip()] = safe_float(value)
    return zones


def query_zone_sizing_heating(conn):
    """Zone design heating loads from HVACSizingSummary -> Zone Sensible Heating."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'HVACSizingSummary'
        AND TableName = 'Zone Sensible Heating'
    """)
    zones = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in zones:
            zones[name] = {}
        zones[name][col_name.strip()] = value.strip() if value else ''
    return zones


def query_zone_info(conn):
    """Zone area, volume, wall area, window area from Zone Summary."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'InputVerificationandResultsSummary'
        AND TableName = 'Zone Summary'
    """)
    zones = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in zones:
            zones[name] = {}
        zones[name][col_name.strip()] = value.strip() if value else ''
    return zones


def query_heating_coils_equipment(conn):
    """Heating coil capacity and airloop from EquipmentSummary -> Heating Coils."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'EquipmentSummary'
        AND TableName = 'Heating Coils'
    """)
    coils = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in coils:
            coils[name] = {}
        coils[name][col_name.strip()] = value.strip() if value else ''
    return coils


def query_heating_coils_sizing(conn):
    """Heating coil design capacity from ComponentSizingSummary -> Coil:Heating:Fuel.
    Also checks for other heating coil types dynamically."""
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT TableName FROM TabularDataWithStrings
        WHERE ReportName = 'ComponentSizingSummary'
        AND (TableName LIKE 'Coil:Heating:%' OR TableName LIKE 'Coil:Heating%')
    """)
    heating_tables = [row[0] for row in c.fetchall()]

    coils = {}
    for table_name in heating_tables:
        c.execute("""
            SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
            WHERE ReportName = 'ComponentSizingSummary'
            AND TableName = ?
        """, (table_name,))
        for row_name, col_name, value in c.fetchall():
            name = row_name.strip().upper()
            if name not in coils:
                coils[name] = {'type': table_name}
            coils[name][col_name.strip()] = value.strip() if value else ''
    return coils


def query_fans(conn):
    """Fan data from EquipmentSummary -> Fans."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'EquipmentSummary'
        AND TableName = 'Fans'
    """)
    fans = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in fans:
            fans[name] = {}
        fans[name][col_name.strip()] = value.strip() if value else ''
    return fans


def query_fan_sizing(conn):
    """Fan sizing from ComponentSizingSummary -> Fan:* tables."""
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT TableName FROM TabularDataWithStrings
        WHERE ReportName = 'ComponentSizingSummary'
        AND TableName LIKE 'Fan:%'
    """)
    fan_tables = [row[0] for row in c.fetchall()]

    fans = {}
    for table_name in fan_tables:
        c.execute("""
            SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
            WHERE ReportName = 'ComponentSizingSummary'
            AND TableName = ?
        """, (table_name,))
        for row_name, col_name, value in c.fetchall():
            name = row_name.strip().upper()
            if name not in fans:
                fans[name] = {'type': table_name}
            fans[name][col_name.strip()] = value.strip() if value else ''
    return fans


def query_airloop_sizing(conn):
    """AirLoopHVAC design air flow from ComponentSizingSummary."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'ComponentSizingSummary'
        AND TableName = 'AirLoopHVAC'
    """)
    loops = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in loops:
            loops[name] = {}
        loops[name][col_name.strip()] = value.strip() if value else ''
    return loops


def query_envelope_walls(conn):
    """Exterior opaque surface areas by zone from EnvelopeSummary -> Opaque Exterior."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'EnvelopeSummary'
        AND TableName = 'Opaque Exterior'
        AND ColumnName IN ('Zone', 'Net Area', 'Gross Area', 'Tilt')
    """)
    surfaces = {}
    for row_name, col_name, value in c.fetchall():
        sname = row_name.strip().upper()
        if sname not in surfaces:
            surfaces[sname] = {}
        surfaces[sname][col_name.strip()] = value.strip() if value else ''

    # Aggregate wall area by zone (Tilt > 45 = wall, Tilt <= 45 = roof)
    zone_walls = {}
    zone_roofs = {}
    for sname, sdata in surfaces.items():
        zone = sdata.get('Zone', '').upper()
        if not zone:
            continue
        area = safe_float(sdata.get('Net Area', '0'))
        tilt = safe_float(sdata.get('Tilt', '90'))
        if tilt > 45:
            zone_walls[zone] = zone_walls.get(zone, 0) + area
        else:
            zone_roofs[zone] = zone_roofs.get(zone, 0) + area

    return zone_walls, zone_roofs


def query_envelope_windows(conn):
    """Exterior fenestration areas by zone from EnvelopeSummary -> Exterior Fenestration."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'EnvelopeSummary'
        AND TableName = 'Exterior Fenestration'
        AND ColumnName IN ('Zone', 'Area of Multiplied Openings', 'Glass Area')
    """)
    surfaces = {}
    for row_name, col_name, value in c.fetchall():
        sname = row_name.strip().upper()
        if sname not in surfaces:
            surfaces[sname] = {}
        surfaces[sname][col_name.strip()] = value.strip() if value else ''

    zone_windows = {}
    for sname, sdata in surfaces.items():
        zone = sdata.get('Zone', '').upper()
        if not zone:
            continue
        area = safe_float(sdata.get('Area of Multiplied Openings',
                                     sdata.get('Glass Area', '0')))
        zone_windows[zone] = zone_windows.get(zone, 0) + area

    return zone_windows


def query_outdoor_air(conn):
    """Mechanical ventilation data from OutdoorAirSummary and OutdoorAirDetails."""
    c = conn.cursor()

    # Average OA during occupied hours
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'OutdoorAirSummary'
        AND TableName = 'Average Outdoor Air During Occupied Hours'
    """)
    zones = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in zones:
            zones[name] = {}
        zones[name][col_name.strip()] = value.strip() if value else ''

    # Design zone OA (Voz in m3/s)
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'OutdoorAirDetails'
        AND TableName = 'Mechanical Ventilation Parameters by Zone'
        AND ColumnName = 'Design Zone Outdoor Airflow - Voz'
    """)
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in zones:
            zones[name] = {}
        zones[name]['Voz_m3s'] = value.strip() if value else ''

    return zones


# ---------------------------------------------------------------------------
# Zone-to-equipment matching
# ---------------------------------------------------------------------------

def match_zone_to_airloop(zone_name, airloop_names):
    """Match a zone name to its serving air loop using multiple strategies.

    Strategy 1: PSZ-AC naming convention (PSZ-AC <ZONE_NAME>)
    Strategy 2: Zone name embedded in air loop name
    Strategy 3: Partial word overlap (>=60% of zone words)

    Returns (airloop_name, match_method) or (None, None).
    """
    zone_upper = zone_name.upper()

    # Strategy 1: PSZ-AC <zone_name>
    psz_name = f"PSZ-AC {zone_upper}"
    if psz_name in airloop_names:
        return psz_name, "PSZ-AC"

    # Strategy 2: Zone name appears in any air loop name
    for loop in airloop_names:
        if zone_upper in loop:
            return loop, "name-embed"

    # Strategy 3: Partial match — zone name words appear in loop name
    zone_words = set(zone_upper.split())
    best_match = None
    best_score = 0
    for loop in airloop_names:
        loop_words = set(loop.split())
        overlap = len(zone_words & loop_words)
        if overlap > best_score and overlap >= len(zone_words) * 0.6:
            best_score = overlap
            best_match = loop

    if best_match:
        return best_match, "partial"

    return None, None


def match_zone_to_coil(zone_name, coil_names, airloop_name=None):
    """Match a zone to its heating coil.

    Uses the air loop name if available, then falls back to zone name matching.
    """
    zone_upper = zone_name.upper()

    # If we have an air loop name, look for coils on that loop
    if airloop_name:
        for coil in coil_names:
            if airloop_name in coil:
                return coil, "via-airloop"

    # Direct: PSZ-AC <zone> GAS HTG COIL
    direct = f"PSZ-AC {zone_upper} GAS HTG COIL"
    if direct in coil_names:
        return direct, "PSZ-AC-coil"

    # Zone name embedded in coil name
    for coil in coil_names:
        if zone_upper in coil:
            return coil, "name-embed-coil"

    return None, None


def match_zone_to_fan(zone_name, fan_names, airloop_name=None):
    """Match a zone to its fan using air loop name or zone name."""
    zone_upper = zone_name.upper()

    if airloop_name:
        for fan in fan_names:
            if airloop_name in fan:
                return fan, "via-airloop"

    # Zone name embedded in fan name
    for fan in fan_names:
        if zone_upper in fan:
            return fan, "name-embed-fan"

    return None, None


# ---------------------------------------------------------------------------
# Per-run diagnostic extraction
# ---------------------------------------------------------------------------

def extract_run_diagnostics(run_dir):
    """Extract the complete per-zone diagnostic dataset for a single run.

    Returns a dict keyed by UPPERCASED zone name, each containing the
    diagnostic metrics for that zone.
    """
    sql_path = find_sql(run_dir)
    conn = sqlite3.connect(sql_path)

    # Gather all data
    zone_unmet = query_zone_unmet(conn)
    zone_sizing = query_zone_sizing_heating(conn)
    zone_info = query_zone_info(conn)
    coils_equip = query_heating_coils_equipment(conn)
    coils_sizing = query_heating_coils_sizing(conn)
    fans_equip = query_fans(conn)
    fans_sizing = query_fan_sizing(conn)
    airloop_sizing = query_airloop_sizing(conn)
    zone_walls, zone_roofs = query_envelope_walls(conn)
    zone_windows = query_envelope_windows(conn)
    outdoor_air = query_outdoor_air(conn)

    conn.close()

    # Build name lookup sets for matching
    airloop_names = set(airloop_sizing.keys())
    coil_names = set(coils_sizing.keys())
    fan_equip_names = set(fans_equip.keys())

    # Union of all zones across data sources
    all_zones = sorted(
        set(zone_unmet.keys()) |
        set(zone_sizing.keys()) |
        set(zone_info.keys())
    )

    zone_data = {}

    for zone in all_zones:
        unmet = zone_unmet.get(zone, {})
        sizing = zone_sizing.get(zone, {})
        info = zone_info.get(zone, {})
        oa = outdoor_air.get(zone, {})

        # --- Unmet hours ---
        occ_htg = unmet.get('During Occupied Heating', 0.0)
        occ_clg = unmet.get('During Occupied Cooling', 0.0)
        tot_htg = unmet.get('During Heating', 0.0)
        tot_clg = unmet.get('During Cooling', 0.0)

        # --- Zone sizing ---
        design_htg_load_w = safe_float(sizing.get('Calculated Design Load', '0'))
        design_air_flow = safe_float(sizing.get('Calculated Design Air Flow', '0'))

        # --- Zone geometry ---
        floor_area = safe_float(info.get('Area', '0'))
        volume = safe_float(info.get('Volume', '0'))
        ext_wall_area = zone_walls.get(zone, 0.0)
        ext_window_area = zone_windows.get(zone, 0.0)

        # --- Equipment matching ---
        airloop_name, _ = match_zone_to_airloop(zone, airloop_names)
        coil_name, _ = match_zone_to_coil(zone, coil_names, airloop_name)
        fan_name, _ = match_zone_to_fan(zone, fan_equip_names, airloop_name)

        # --- Coil capacity ---
        coil_capacity_w = None
        if coil_name and coil_name in coils_sizing:
            coil_data = coils_sizing[coil_name]
            cap_str = coil_data.get('Design Size Nominal Capacity',
                                     coil_data.get('User-Specified Nominal Capacity', '0'))
            coil_capacity_w = safe_float(cap_str)

        # Also check equipment summary for Nominal Total Capacity
        if coil_capacity_w in (None, 0.0) and coil_name:
            eq_coil = coils_equip.get(coil_name, {})
            coil_capacity_w = safe_float(eq_coil.get('Nominal Total Capacity', '0'))

        # --- Fan data ---
        fan_flow_m3s = None
        if fan_name and fan_name in fans_equip:
            fd = fans_equip[fan_name]
            fan_flow_m3s = safe_float(fd.get('Max Air Flow Rate', '0'))

        # Fan sizing fallback
        if fan_name:
            for fsn in fans_sizing:
                if fan_name in fsn or fsn in fan_name:
                    fs = fans_sizing[fsn]
                    sizing_flow = safe_float(fs.get('Design Size Maximum Flow Rate', '0'))
                    if sizing_flow > 0 and (fan_flow_m3s is None or fan_flow_m3s == 0):
                        fan_flow_m3s = sizing_flow
                    break

        # --- Air loop design flow ---
        system_air_flow = None
        if airloop_name and airloop_name in airloop_sizing:
            als = airloop_sizing[airloop_name]
            system_air_flow = safe_float(als.get('Design Supply Air Flow Rate', '0'))

        # --- OA data ---
        voz_m3s = safe_float(oa.get('Voz_m3s', '0'))
        mech_vent_ach = safe_float(oa.get('Mechanical Ventilation', '0'))
        avg_oa_m3s = mech_vent_ach * volume / 3600.0 if volume > 0 else 0.0

        # --- Calculated ratios ---
        capacity_ratio = safe_div(coil_capacity_w, design_htg_load_w)
        envelope_exposure = safe_div(ext_wall_area + ext_window_area, floor_area)
        supply_flow = system_air_flow if system_air_flow else fan_flow_m3s
        oa_flow_for_frac = voz_m3s if voz_m3s > 0 else avg_oa_m3s
        vent_fraction = safe_div(oa_flow_for_frac, supply_flow) if supply_flow else None

        zone_data[zone] = {
            'occ_htg_unmet_hr': occ_htg,
            'occ_clg_unmet_hr': occ_clg,
            'tot_htg_unmet_hr': tot_htg,
            'tot_clg_unmet_hr': tot_clg,
            'design_htg_load_w': design_htg_load_w,
            'design_air_flow_m3s': design_air_flow,
            'coil_capacity_w': coil_capacity_w,
            'capacity_ratio': capacity_ratio,
            'floor_area_m2': floor_area,
            'volume_m3': volume,
            'ext_wall_area_m2': ext_wall_area,
            'ext_window_area_m2': ext_window_area,
            'envelope_exposure_ratio': envelope_exposure,
            'vent_fraction': vent_fraction,
            'voz_m3s': voz_m3s,
            'fan_flow_m3s': fan_flow_m3s,
            'system_air_flow_m3s': system_air_flow,
        }

    return zone_data


# ---------------------------------------------------------------------------
# Comparison assembly
# ---------------------------------------------------------------------------

def compute_delta(val_new, val_old):
    """Compute absolute delta between two values, returning None if either is None."""
    if val_new is None or val_old is None:
        return None
    return val_new - val_old


def compute_pct_change(val_new, val_old):
    """Compute percentage change from old to new, returning None if not computable."""
    if val_new is None or val_old is None:
        return None
    if val_old == 0:
        if val_new == 0:
            return 0.0
        return None  # Infinite change
    return ((val_new - val_old) / abs(val_old)) * 100.0


def build_comparison(run_dirs, labels):
    """Build the complete multi-run comparison dataset.

    Returns a dict with:
        - runs: list of per-run summaries
        - zones: list of per-zone comparison records
    """
    # Extract diagnostics for each run
    run_zone_data = []
    for rd in run_dirs:
        run_zone_data.append(extract_run_diagnostics(rd))

    # Compute union of all zones across all runs
    all_zones = set()
    for zd in run_zone_data:
        all_zones |= set(zd.keys())
    all_zones = sorted(all_zones)

    # Per-run facility summaries
    runs_summary = []
    for i, (rd, label) in enumerate(zip(run_dirs, labels)):
        zd = run_zone_data[i]
        total_htg = sum(z['occ_htg_unmet_hr'] for z in zd.values())
        total_clg = sum(z['occ_clg_unmet_hr'] for z in zd.values())
        runs_summary.append({
            'label': label,
            'dir': os.path.abspath(rd),
            'zone_count': len(zd),
            'total_htg_unmet': round(total_htg, 1),
            'total_clg_unmet': round(total_clg, 1),
        })

    # Metrics to compare (key, display_name, precision)
    compare_metrics = [
        ('occ_htg_unmet_hr', 'Occ Htg (hr)', 1),
        ('occ_clg_unmet_hr', 'Occ Clg (hr)', 1),
        ('design_htg_load_w', 'Design Load (W)', 0),
        ('coil_capacity_w', 'Coil Cap (W)', 0),
        ('capacity_ratio', 'Cap Ratio', 2),
        ('envelope_exposure_ratio', 'Envelope Exp', 2),
        ('vent_fraction', 'Vent Frac', 2),
    ]

    # Build per-zone comparison records
    zone_records = []
    for zone in all_zones:
        zone_runs = []
        for i, label in enumerate(labels):
            zd = run_zone_data[i]
            if zone in zd:
                z = zd[zone]
                zone_runs.append({
                    'label': label,
                    'present': True,
                    'occ_htg_unmet_hr': z['occ_htg_unmet_hr'],
                    'occ_clg_unmet_hr': z['occ_clg_unmet_hr'],
                    'tot_htg_unmet_hr': z['tot_htg_unmet_hr'],
                    'tot_clg_unmet_hr': z['tot_clg_unmet_hr'],
                    'design_htg_load_w': z['design_htg_load_w'],
                    'design_air_flow_m3s': z['design_air_flow_m3s'],
                    'coil_capacity_w': z['coil_capacity_w'],
                    'capacity_ratio': z['capacity_ratio'],
                    'floor_area_m2': z['floor_area_m2'],
                    'volume_m3': z['volume_m3'],
                    'ext_wall_area_m2': z['ext_wall_area_m2'],
                    'ext_window_area_m2': z['ext_window_area_m2'],
                    'envelope_exposure_ratio': z['envelope_exposure_ratio'],
                    'vent_fraction': z['vent_fraction'],
                    'voz_m3s': z['voz_m3s'],
                    'fan_flow_m3s': z['fan_flow_m3s'],
                    'system_air_flow_m3s': z['system_air_flow_m3s'],
                })
            else:
                zone_runs.append({
                    'label': label,
                    'present': False,
                })

        # Compute deltas between consecutive runs
        deltas = []
        for j in range(1, len(zone_runs)):
            prev = zone_runs[j - 1]
            curr = zone_runs[j]
            if not prev['present'] or not curr['present']:
                deltas.append({
                    'from': labels[j - 1],
                    'to': labels[j],
                    'computable': False,
                })
                continue

            delta_record = {
                'from': labels[j - 1],
                'to': labels[j],
                'computable': True,
            }
            for metric_key, _, _ in compare_metrics:
                val_old = prev.get(metric_key)
                val_new = curr.get(metric_key)
                delta_record[f'{metric_key}_delta'] = compute_delta(val_new, val_old)
                delta_record[f'{metric_key}_pct'] = compute_pct_change(val_new, val_old)

            deltas.append(delta_record)

        zone_records.append({
            'zone': zone,
            'runs': zone_runs,
            'deltas': deltas,
        })

    return {
        'runs': runs_summary,
        'zones': zone_records,
        'compare_metrics': compare_metrics,
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_markdown(comparison, threshold=0):
    """Format the multi-run comparison as markdown tables."""
    lines = []
    runs = comparison['runs']
    zones = comparison['zones']
    num_runs = len(runs)
    labels = [r['label'] for r in runs]

    # --- Determine which zones pass threshold ---
    def zone_max_unmet(zrec):
        """Maximum occupied unmet hours (htg or clg) across any run."""
        max_val = 0.0
        for r in zrec['runs']:
            if r['present']:
                max_val = max(max_val,
                              r.get('occ_htg_unmet_hr', 0),
                              r.get('occ_clg_unmet_hr', 0))
        return max_val

    shown_zones = [z for z in zones if zone_max_unmet(z) > threshold]

    # Sort by worst unmet in final run (descending)
    def final_run_htg(zrec):
        last = zrec['runs'][-1]
        if last['present']:
            return last.get('occ_htg_unmet_hr', 0)
        return 0.0
    shown_zones.sort(key=final_run_htg, reverse=True)

    # === Header ===
    lines.append("# Multi-Run Zone Diagnostic Comparison")
    lines.append("")

    # === Run summary table ===
    lines.append("## Facility Summary")
    lines.append("")
    header = "| Metric |"
    sep = "|--------|"
    for r in runs:
        header += f" {r['label']} |"
        sep += "--------|"
    # Add delta columns
    for j in range(1, num_runs):
        header += f" {labels[j-1]}->{labels[j]} |"
        sep += "--------|"
    lines.append(header)
    lines.append(sep)

    # Total htg unmet row
    row = "| Total Htg Unmet (hr) |"
    for r in runs:
        row += f" {r['total_htg_unmet']:.1f} |"
    for j in range(1, num_runs):
        delta = runs[j]['total_htg_unmet'] - runs[j-1]['total_htg_unmet']
        row += f" {fmt_delta(delta)} |"
    lines.append(row)

    # Total clg unmet row
    row = "| Total Clg Unmet (hr) |"
    for r in runs:
        row += f" {r['total_clg_unmet']:.1f} |"
    for j in range(1, num_runs):
        delta = runs[j]['total_clg_unmet'] - runs[j-1]['total_clg_unmet']
        row += f" {fmt_delta(delta)} |"
    lines.append(row)

    # Zone count row
    row = "| Zones Analyzed |"
    for r in runs:
        row += f" {r['zone_count']} |"
    for j in range(1, num_runs):
        row += " - |"
    lines.append(row)

    lines.append("")
    lines.append(f"**Zones shown:** {len(shown_zones)} (threshold: >{threshold} hr in any run)")
    lines.append("")

    # === Main comparison table: Heating Unmet Hours ===
    lines.append("## Heating Unmet Hours Comparison")
    lines.append("")

    header = "| Zone |"
    sep = "|------|"
    for label in labels:
        header += f" {label} Htg (hr) |"
        sep += "------------|"
    for j in range(1, num_runs):
        header += f" {labels[j-1]}->{labels[j]} |"
        sep += "------------|"
    lines.append(header)
    lines.append(sep)

    for zrec in shown_zones:
        row = f"| {zrec['zone']} |"
        for r in zrec['runs']:
            if r['present']:
                row += f" {r['occ_htg_unmet_hr']:.1f} |"
            else:
                row += " N/A |"
        for d in zrec['deltas']:
            if d['computable']:
                dv = d.get('occ_htg_unmet_hr_delta')
                row += f" {fmt_delta(dv)} |"
            else:
                row += " N/A |"
        lines.append(row)

    lines.append("")

    # === Capacity & Sizing Comparison ===
    lines.append("## Capacity & Sizing Comparison")
    lines.append("")

    header = "| Zone |"
    sep = "|------|"
    for label in labels:
        header += f" {label} Load (kW) | {label} Cap (kW) | {label} Ratio |"
        sep += "------------|------------|----------|"
    lines.append(header)
    lines.append(sep)

    for zrec in shown_zones:
        row = f"| {zrec['zone']} |"
        for r in zrec['runs']:
            if r['present']:
                load_kw = r['design_htg_load_w'] / 1000.0 if r['design_htg_load_w'] else None
                cap_kw = r['coil_capacity_w'] / 1000.0 if r['coil_capacity_w'] else None
                row += f" {fmt_val(load_kw)} | {fmt_val(cap_kw)} | {fmt_val(r['capacity_ratio'], 2)} |"
            else:
                row += " N/A | N/A | N/A |"
        lines.append(row)

    lines.append("")

    # === Delta Summary: Most Improved / Most Regressed ===
    lines.append("## Delta Summary")
    lines.append("")

    for j in range(1, num_runs):
        tag = f"{labels[j-1]} -> {labels[j]}"
        lines.append(f"### {tag}")
        lines.append("")

        # Collect computable heating deltas
        htg_deltas = []
        for zrec in zones:
            d = zrec['deltas'][j - 1] if j - 1 < len(zrec['deltas']) else None
            if d and d['computable']:
                dv = d.get('occ_htg_unmet_hr_delta')
                if dv is not None:
                    htg_deltas.append((zrec['zone'], dv, d.get('occ_htg_unmet_hr_pct')))

        if not htg_deltas:
            lines.append("No computable deltas for this transition.")
            lines.append("")
            continue

        # Most improved (largest decrease in unmet hours)
        improved = sorted(htg_deltas, key=lambda x: x[1])
        lines.append("**Most Improved (Heating Unmet Decreased):**")
        lines.append("")
        lines.append("| Zone | Delta (hr) | Change (%) |")
        lines.append("|------|-----------|-----------|")
        count = 0
        for zone_name, delta, pct in improved:
            if delta >= 0:
                break
            lines.append(f"| {zone_name} | {fmt_delta(delta)} | {fmt_pct(pct)} |")
            count += 1
            if count >= 10:
                break
        if count == 0:
            lines.append("| (none) | - | - |")
        lines.append("")

        # Most regressed (largest increase in unmet hours)
        regressed = sorted(htg_deltas, key=lambda x: x[1], reverse=True)
        lines.append("**Most Regressed (Heating Unmet Increased):**")
        lines.append("")
        lines.append("| Zone | Delta (hr) | Change (%) |")
        lines.append("|------|-----------|-----------|")
        count = 0
        for zone_name, delta, pct in regressed:
            if delta <= 0:
                break
            lines.append(f"| {zone_name} | {fmt_delta(delta)} | {fmt_pct(pct)} |")
            count += 1
            if count >= 10:
                break
        if count == 0:
            lines.append("| (none) | - | - |")
        lines.append("")

    return "\n".join(lines)


def format_json_output(comparison, threshold=0):
    """Format the comparison as a JSON structure."""
    runs = comparison['runs']
    zones = comparison['zones']

    def zone_max_unmet(zrec):
        max_val = 0.0
        for r in zrec['runs']:
            if r['present']:
                max_val = max(max_val,
                              r.get('occ_htg_unmet_hr', 0),
                              r.get('occ_clg_unmet_hr', 0))
        return max_val

    shown_zones = [z for z in zones if zone_max_unmet(z) > threshold]

    def final_run_htg(zrec):
        last = zrec['runs'][-1]
        if last['present']:
            return last.get('occ_htg_unmet_hr', 0)
        return 0.0
    shown_zones.sort(key=final_run_htg, reverse=True)

    output_zones = []
    for zrec in shown_zones:
        zone_out = {
            'zone': zrec['zone'],
            'runs': [],
            'deltas': [],
        }
        for r in zrec['runs']:
            if r['present']:
                zone_out['runs'].append({
                    'label': r['label'],
                    'occ_htg': r['occ_htg_unmet_hr'],
                    'occ_clg': r['occ_clg_unmet_hr'],
                    'design_load_w': r['design_htg_load_w'],
                    'coil_cap_w': r['coil_capacity_w'],
                    'cap_ratio': r['capacity_ratio'],
                    'envelope_exposure': r['envelope_exposure_ratio'],
                    'vent_fraction': r['vent_fraction'],
                })
            else:
                zone_out['runs'].append({
                    'label': r['label'],
                    'present': False,
                })

        for d in zrec['deltas']:
            if d['computable']:
                delta_out = {
                    'from': d['from'],
                    'to': d['to'],
                    'occ_htg_delta': d.get('occ_htg_unmet_hr_delta'),
                    'occ_htg_pct': d.get('occ_htg_unmet_hr_pct'),
                    'occ_clg_delta': d.get('occ_clg_unmet_hr_delta'),
                    'cap_ratio_delta': d.get('capacity_ratio_delta'),
                }
                zone_out['deltas'].append(delta_out)
            else:
                zone_out['deltas'].append({
                    'from': d['from'],
                    'to': d['to'],
                    'computable': False,
                })

        output_zones.append(zone_out)

    result = {
        'runs': runs,
        'zones': output_zones,
    }

    return json.dumps(result, indent=2, default=str)


def format_csv_output(comparison, threshold=0):
    """Format the comparison as CSV with one row per zone, columns for each run."""
    runs = comparison['runs']
    zones = comparison['zones']
    labels = [r['label'] for r in runs]
    num_runs = len(runs)

    def zone_max_unmet(zrec):
        max_val = 0.0
        for r in zrec['runs']:
            if r['present']:
                max_val = max(max_val,
                              r.get('occ_htg_unmet_hr', 0),
                              r.get('occ_clg_unmet_hr', 0))
        return max_val

    shown_zones = [z for z in zones if zone_max_unmet(z) > threshold]

    def final_run_htg(zrec):
        last = zrec['runs'][-1]
        if last['present']:
            return last.get('occ_htg_unmet_hr', 0)
        return 0.0
    shown_zones.sort(key=final_run_htg, reverse=True)

    output = io.StringIO()
    writer = csv.writer(output)

    # Build header
    headers = ['Zone']
    for label in labels:
        headers.extend([
            f'{label} Occ Htg (hr)',
            f'{label} Occ Clg (hr)',
            f'{label} Design Load (W)',
            f'{label} Coil Cap (W)',
            f'{label} Cap Ratio',
            f'{label} Env Exposure',
            f'{label} Vent Frac',
        ])
    for j in range(1, num_runs):
        tag = f'{labels[j-1]}->{labels[j]}'
        headers.extend([
            f'{tag} Htg Delta',
            f'{tag} Htg %',
            f'{tag} Clg Delta',
            f'{tag} Cap Ratio Delta',
        ])
    writer.writerow(headers)

    for zrec in shown_zones:
        row = [zrec['zone']]
        for r in zrec['runs']:
            if r['present']:
                row.extend([
                    r['occ_htg_unmet_hr'],
                    r['occ_clg_unmet_hr'],
                    r['design_htg_load_w'],
                    r['coil_capacity_w'],
                    r['capacity_ratio'],
                    r['envelope_exposure_ratio'],
                    r['vent_fraction'],
                ])
            else:
                row.extend(['N/A'] * 7)
        for d in zrec['deltas']:
            if d['computable']:
                row.extend([
                    d.get('occ_htg_unmet_hr_delta'),
                    d.get('occ_htg_unmet_hr_pct'),
                    d.get('occ_clg_unmet_hr_delta'),
                    d.get('capacity_ratio_delta'),
                ])
            else:
                row.extend(['N/A'] * 4)
        writer.writerow(row)

    return output.getvalue()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Multi-run EnergyPlus diagnostic comparison — '
                    'side-by-side zone metrics with deltas between iterations')
    parser.add_argument('run_dirs', nargs='+', metavar='RUN_DIR',
                        help='Two or more EnergyPlus run directories containing eplusout.sql')
    parser.add_argument('--labels', nargs='+', metavar='LABEL',
                        help='Human-readable labels for each run (must match number of run dirs)')
    parser.add_argument('--threshold', type=float, default=0,
                        help='Only show zones with > N occupied unmet hours in ANY run (default: 0)')
    parser.add_argument('--output', help='Write primary output to file instead of stdout')
    parser.add_argument('--json', dest='json_file', help='Write JSON output to file (independent of --format)')
    parser.add_argument('--format', choices=['markdown', 'json', 'csv'],
                        default='markdown', help='Output format (default: markdown)')
    args = parser.parse_args()

    # Validate inputs
    if len(args.run_dirs) < 2:
        print("ERROR: At least two run directories are required.", file=sys.stderr)
        sys.exit(1)

    # Assign labels
    if args.labels:
        if len(args.labels) != len(args.run_dirs):
            print(f"ERROR: Number of labels ({len(args.labels)}) must match "
                  f"number of run directories ({len(args.run_dirs)}).", file=sys.stderr)
            sys.exit(1)
        labels = args.labels
    else:
        labels = [f"Run {i+1}" for i in range(len(args.run_dirs))]

    # Verify all run directories exist and have SQL files
    for rd in args.run_dirs:
        if not os.path.isdir(rd):
            print(f"ERROR: Run directory does not exist: {rd}", file=sys.stderr)
            sys.exit(1)
        find_sql(rd)  # Will sys.exit if not found

    # Build the comparison
    comparison = build_comparison(args.run_dirs, labels)

    # Format primary output
    if args.format == 'json':
        output = format_json_output(comparison, args.threshold)
    elif args.format == 'csv':
        output = format_csv_output(comparison, args.threshold)
    else:
        output = format_markdown(comparison, args.threshold)

    # Write primary output
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"Output written to {args.output}")
    else:
        print(output)

    # Write JSON sidecar if requested (independent of --format)
    if args.json_file:
        json_output = format_json_output(comparison, args.threshold)
        with open(args.json_file, 'w', encoding='utf-8') as f:
            f.write(json_output)
        print(f"JSON output written to {args.json_file}")


if __name__ == '__main__':
    main()
