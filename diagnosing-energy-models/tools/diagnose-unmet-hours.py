"""
diagnose-unmet-hours.py — Per-zone diagnostic cross-reference for EnergyPlus unmet hours

For any EnergyPlus model, produces a per-zone diagnostic table that cross-references
unmet hours with equipment sizing, envelope exposure, and ventilation characteristics
to identify root causes. This is the "first look" tool an engineer runs to understand
why a model has unmet hours.

Usage:
    python diagnose-unmet-hours.py <run-dir>
    python diagnose-unmet-hours.py <run-dir> --format markdown
    python diagnose-unmet-hours.py <run-dir> --format json
    python diagnose-unmet-hours.py <run-dir> --format csv
    python diagnose-unmet-hours.py <run-dir> --output diagnostics.md
    python diagnose-unmet-hours.py <run-dir> --threshold 50

Arguments:
    run-dir     Path to the EnergyPlus run directory containing eplusout.sql.
                Accepts the run/ subdirectory itself or the parent workflow dir
                (auto-detects run/eplusout.sql if needed).

Options:
    --format FORMAT   Output format: markdown (default), json, csv
    --output FILE     Write output to file instead of stdout
    --threshold N     Only show zones with > N occupied heating unmet hours (default: 0)

Per-Zone Diagnostic Data:
    - Occupied heating/cooling unmet hours
    - Design heating load (W) and peak outdoor temp (C)
    - Heating coil capacity (W) serving the zone
    - Capacity-to-load ratio (< 1.0 = undersized)
    - Zone design air flow (m3/s) vs system air flow capacity (m3/s)
    - Fan capacity (m3/s and Pa)
    - Zone volume (m3), floor area (m2), ceiling height (m)
    - Exterior wall area (m2) and window area (m2)
    - Envelope exposure ratio (ext_surface / floor_area)
    - Volume/area ratio (thermal mass proxy)
    - Thermostat schedule name

Python: 3.x (stdlib only - sqlite3, json, csv, sys, os, argparse, math)
"""

import sqlite3
import json
import csv
import sys
import os
import argparse
import math
import io
import re


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


def find_err(run_dir):
    """Locate eplusout.err."""
    for candidate in [
        os.path.join(run_dir, 'eplusout.err'),
        os.path.join(run_dir, 'run', 'eplusout.err'),
    ]:
        if os.path.exists(candidate):
            return candidate
    return None


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
    # Discover all heating coil table names in ComponentSizingSummary
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
    """Fan sizing from ComponentSizingSummary -> Fan:ConstantVolume (and other fan types)."""
    c = conn.cursor()
    # Discover all fan table names
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
    # Each RowName is a surface name; we aggregate by zone
    surfaces = {}
    for row_name, col_name, value in c.fetchall():
        sname = row_name.strip().upper()
        if sname not in surfaces:
            surfaces[sname] = {}
        surfaces[sname][col_name.strip()] = value.strip() if value else ''

    # Aggregate wall area by zone (Tilt ~= 90 deg is wall, ~= 0 deg is roof)
    zone_walls = {}
    zone_roofs = {}
    for sname, sdata in surfaces.items():
        zone = sdata.get('Zone', '').upper()
        if not zone:
            continue
        area = safe_float(sdata.get('Net Area', '0'))
        tilt = safe_float(sdata.get('Tilt', '90'))
        if tilt > 45:
            # Wall
            zone_walls[zone] = zone_walls.get(zone, 0) + area
        else:
            # Roof
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

    # Aggregate window area by zone
    zone_windows = {}
    for sname, sdata in surfaces.items():
        zone = sdata.get('Zone', '').upper()
        if not zone:
            continue
        # Use multiplied openings area (accounts for zone multipliers)
        area = safe_float(sdata.get('Area of Multiplied Openings',
                                     sdata.get('Glass Area', '0')))
        zone_windows[zone] = zone_windows.get(zone, 0) + area

    return zone_windows


def query_thermostat_schedules(conn):
    """Thermostat schedule names from SystemSummary -> Thermostat Schedules."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'SystemSummary'
        AND TableName = 'Thermostat Schedules'
    """)
    zones = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in zones:
            zones[name] = {}
        zones[name][col_name.strip()] = value.strip() if value else ''
    return zones


def query_outdoor_air(conn):
    """Mechanical ventilation data from OutdoorAirSummary and OutdoorAirDetails.

    OutdoorAirSummary reports average OA in ach (air changes per hour).
    OutdoorAirDetails reports design zone OA (Voz) in m3/s.
    We use both: ach for the average rate and Voz for ventilation fraction calculations.
    """
    c = conn.cursor()

    # Average OA during occupied hours (units: ach for Mechanical Ventilation)
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

    # Design zone OA from OA Details (units: m3/s for Voz)
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
    Strategy 3: Special naming patterns (e.g., POOL DEHUMIDIFIER <ZONE_NAME>)

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

    # Direct: PSZ-AC <zone> FAN
    for fan in fan_names:
        if zone_upper in fan:
            return fan, "name-embed-fan"

    return None, None


# ---------------------------------------------------------------------------
# Main diagnostic assembly
# ---------------------------------------------------------------------------

def build_diagnostics(run_dir):
    """Build the complete per-zone diagnostic dataset."""
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
    thermostat_scheds = query_thermostat_schedules(conn)
    outdoor_air = query_outdoor_air(conn)

    conn.close()

    # Build name lookup sets for matching
    airloop_names = set(airloop_sizing.keys())
    coil_names = set(coils_sizing.keys())
    fan_equip_names = set(fans_equip.keys())
    fan_sizing_names = set(fans_sizing.keys())

    # Also add coils from equipment summary
    for cname in coils_equip:
        # Equipment summary uses different key structure; add to lookup
        pass

    # Union of all zones across data sources
    all_zones = sorted(
        set(zone_unmet.keys()) |
        set(zone_sizing.keys()) |
        set(zone_info.keys())
    )

    diagnostics = []
    match_log = []

    for zone in all_zones:
        unmet = zone_unmet.get(zone, {})
        sizing = zone_sizing.get(zone, {})
        info = zone_info.get(zone, {})
        thermo = thermostat_scheds.get(zone, {})
        oa = outdoor_air.get(zone, {})

        # --- Unmet hours ---
        occ_htg = unmet.get('During Occupied Heating', 0.0)
        occ_clg = unmet.get('During Occupied Cooling', 0.0)
        tot_htg = unmet.get('During Heating', 0.0)
        tot_clg = unmet.get('During Cooling', 0.0)

        # --- Zone sizing ---
        design_htg_load_w = safe_float(sizing.get('Calculated Design Load', '0'))
        design_air_flow = safe_float(sizing.get('Calculated Design Air Flow', '0'))
        peak_oat = sizing.get('Outdoor Temperature at Peak Load', 'N/A')
        peak_datetime = sizing.get('Date/Time Of Peak {TIMESTAMP}', 'N/A')
        min_oa_flow = safe_float(sizing.get('Minimum Outdoor Air Flow Rate', '0'))

        # --- Zone geometry ---
        floor_area = safe_float(info.get('Area', '0'))
        volume = safe_float(info.get('Volume', '0'))
        ext_wall_area = zone_walls.get(zone, 0.0)
        ext_window_area = zone_windows.get(zone, 0.0)
        ext_roof_area = zone_roofs.get(zone, 0.0)

        # Ceiling height derived from volume / floor_area
        ceiling_height = safe_div(volume, floor_area)

        # --- Equipment matching ---
        airloop_name, airloop_method = match_zone_to_airloop(zone, airloop_names)
        coil_name, coil_method = match_zone_to_coil(zone, coil_names, airloop_name)
        fan_name, fan_method = match_zone_to_fan(zone, fan_equip_names, airloop_name)

        match_log.append({
            'zone': zone,
            'airloop': airloop_name, 'airloop_method': airloop_method,
            'coil': coil_name, 'coil_method': coil_method,
            'fan': fan_name, 'fan_method': fan_method,
        })

        # --- Coil capacity ---
        coil_capacity_w = None
        if coil_name and coil_name in coils_sizing:
            coil_data = coils_sizing[coil_name]
            # Try design size first, then user-specified
            cap_str = coil_data.get('Design Size Nominal Capacity',
                                     coil_data.get('User-Specified Nominal Capacity', '0'))
            coil_capacity_w = safe_float(cap_str)

        # Also check equipment summary for Nominal Total Capacity
        if coil_capacity_w in (None, 0.0) and coil_name:
            eq_coil = coils_equip.get(coil_name, {})
            coil_capacity_w = safe_float(eq_coil.get('Nominal Total Capacity', '0'))

        # --- Fan data ---
        fan_flow_m3s = None
        fan_pressure_pa = None
        if fan_name and fan_name in fans_equip:
            fd = fans_equip[fan_name]
            fan_flow_m3s = safe_float(fd.get('Max Air Flow Rate', '0'))
            fan_pressure_pa = safe_float(fd.get('Delta Pressure', '0'))

        # Fan sizing data (may have design values)
        if fan_name:
            # Try matching to fan sizing (names may differ slightly)
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
        # Voz is design zone OA in m3/s; Mechanical Ventilation is average in ach
        voz_m3s = safe_float(oa.get('Voz_m3s', '0'))
        mech_vent_ach = safe_float(oa.get('Mechanical Ventilation', '0'))
        # Convert average ach to m3/s for reference: flow = ach * volume / 3600
        avg_oa_m3s = mech_vent_ach * volume / 3600.0 if volume > 0 else 0.0

        # --- Thermostat ---
        htg_schedule = thermo.get('Heating Schedule', 'N/A')

        # --- Calculated ratios ---
        capacity_ratio = safe_div(coil_capacity_w, design_htg_load_w)
        envelope_exposure = safe_div(ext_wall_area + ext_window_area, floor_area)
        volume_per_area = safe_div(volume, floor_area)
        supply_flow = system_air_flow if system_air_flow else fan_flow_m3s
        # Use Voz (m3/s) for vent fraction; fall back to converted avg ach if Voz unavailable
        oa_flow_for_frac = voz_m3s if voz_m3s > 0 else avg_oa_m3s
        vent_fraction = safe_div(oa_flow_for_frac, supply_flow) if supply_flow else None

        diagnostics.append({
            'zone': zone,
            # Unmet hours
            'occ_htg_unmet_hr': occ_htg,
            'occ_clg_unmet_hr': occ_clg,
            'tot_htg_unmet_hr': tot_htg,
            'tot_clg_unmet_hr': tot_clg,
            # Zone sizing
            'design_htg_load_w': design_htg_load_w,
            'design_air_flow_m3s': design_air_flow,
            'peak_oat_c': peak_oat,
            'peak_datetime': peak_datetime,
            'min_oa_flow_m3s': min_oa_flow,
            # Equipment
            'airloop': airloop_name or 'N/A',
            'coil_name': coil_name or 'N/A',
            'coil_capacity_w': coil_capacity_w,
            'capacity_ratio': capacity_ratio,
            'fan_name': fan_name or 'N/A',
            'fan_flow_m3s': fan_flow_m3s,
            'fan_pressure_pa': fan_pressure_pa,
            'system_air_flow_m3s': system_air_flow,
            # Geometry
            'floor_area_m2': floor_area,
            'volume_m3': volume,
            'ceiling_height_m': ceiling_height,
            'ext_wall_area_m2': ext_wall_area,
            'ext_window_area_m2': ext_window_area,
            'ext_roof_area_m2': ext_roof_area,
            # Ratios
            'envelope_exposure_ratio': envelope_exposure,
            'volume_per_area': volume_per_area,
            'vent_fraction': vent_fraction,
            # Schedules
            'htg_schedule': htg_schedule,
            # OA
            'voz_m3s': voz_m3s,
            'mech_vent_ach': mech_vent_ach,
            'avg_oa_m3s': avg_oa_m3s,
        })

    return {
        'run_dir': os.path.abspath(run_dir),
        'sql_path': os.path.abspath(find_sql(run_dir)),
        'zone_count': len(diagnostics),
        'diagnostics': diagnostics,
        'match_log': match_log,
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_markdown(data, threshold=0):
    """Format diagnostics as markdown tables."""
    lines = []
    diags = data['diagnostics']

    # Sort by occupied heating unmet descending
    diags_sorted = sorted(diags, key=lambda d: d['occ_htg_unmet_hr'], reverse=True)

    # Filter by threshold
    shown = [d for d in diags_sorted
             if d['occ_htg_unmet_hr'] > threshold or d['occ_clg_unmet_hr'] > threshold]

    lines.append(f"# Unmet Hours Diagnostic Report")
    lines.append(f"")
    lines.append(f"**Run directory:** `{data['run_dir']}`")
    lines.append(f"**Zones analyzed:** {data['zone_count']}")
    lines.append(f"**Zones shown:** {len(shown)} (threshold: >{threshold} hr)")
    lines.append(f"")

    # --- Summary statistics ---
    total_occ_htg = sum(d['occ_htg_unmet_hr'] for d in diags)
    total_occ_clg = sum(d['occ_clg_unmet_hr'] for d in diags)
    zones_over_100 = sum(1 for d in diags if d['occ_htg_unmet_hr'] > 100)
    zones_over_200 = sum(1 for d in diags if d['occ_htg_unmet_hr'] > 200)
    undersized = sum(1 for d in diags
                     if d['capacity_ratio'] is not None and d['capacity_ratio'] < 1.0
                     and d['occ_htg_unmet_hr'] > 0)

    lines.append(f"## Summary Statistics")
    lines.append(f"")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total zone occupied heating unmet hours | {total_occ_htg:.1f} |")
    lines.append(f"| Total zone occupied cooling unmet hours | {total_occ_clg:.1f} |")
    lines.append(f"| Zones with >100 hr occ heating unmet | {zones_over_100} |")
    lines.append(f"| Zones with >200 hr occ heating unmet | {zones_over_200} |")
    lines.append(f"| Zones with capacity ratio <1.0 and unmet >0 | {undersized} |")
    lines.append(f"")

    # --- Main diagnostic table ---
    lines.append(f"## Per-Zone Diagnostic Table")
    lines.append(f"")
    lines.append("| Zone | Occ Htg (hr) | Occ Clg (hr) | Design Load (kW) | Coil Cap (kW) | Cap Ratio | Ext Exposure | Vol/Area (m) | Vent Frac | Peak OAT (C) | Htg Schedule |")
    lines.append("|------|-------------|-------------|-----------------|--------------|-----------|-------------|-------------|-----------|-------------|-------------|")

    for d in shown:
        load_kw = f"{d['design_htg_load_w']/1000:.1f}" if d['design_htg_load_w'] else "N/A"
        cap_kw = f"{d['coil_capacity_w']/1000:.1f}" if d['coil_capacity_w'] else "N/A"
        cap_r = f"{d['capacity_ratio']:.2f}" if d['capacity_ratio'] is not None else "N/A"
        env_exp = f"{d['envelope_exposure_ratio']:.2f}" if d['envelope_exposure_ratio'] is not None else "N/A"
        vol_a = f"{d['volume_per_area']:.1f}" if d['volume_per_area'] is not None else "N/A"
        vf = f"{d['vent_fraction']:.2f}" if d['vent_fraction'] is not None else "N/A"
        oat = d['peak_oat_c'] if d['peak_oat_c'] != 'N/A' else 'N/A'
        sched = d['htg_schedule'] if len(d['htg_schedule']) <= 40 else d['htg_schedule'][:37] + '...'

        lines.append(f"| {d['zone']} | {d['occ_htg_unmet_hr']:.1f} | {d['occ_clg_unmet_hr']:.1f} | {load_kw} | {cap_kw} | {cap_r} | {env_exp} | {vol_a} | {vf} | {oat} | {sched} |")

    lines.append(f"")

    # --- Detailed equipment table ---
    lines.append(f"## Equipment Matching Detail")
    lines.append(f"")
    lines.append("| Zone | Air Loop | Fan Flow (m3/s) | Fan dP (Pa) | System Flow (m3/s) | Design Flow (m3/s) | Mech Vent (m3/s) |")
    lines.append("|------|----------|----------------|------------|-------------------|-------------------|-----------------|")

    for d in shown:
        ff = f"{d['fan_flow_m3s']:.3f}" if d['fan_flow_m3s'] else "N/A"
        fp = f"{d['fan_pressure_pa']:.0f}" if d['fan_pressure_pa'] else "N/A"
        sf = f"{d['system_air_flow_m3s']:.3f}" if d['system_air_flow_m3s'] else "N/A"
        df = f"{d['design_air_flow_m3s']:.3f}" if d['design_air_flow_m3s'] else "N/A"
        mv = f"{d['voz_m3s']:.4f}" if d['voz_m3s'] else "N/A"
        loop = d['airloop'] if len(d['airloop']) <= 40 else d['airloop'][:37] + '...'

        lines.append(f"| {d['zone']} | {loop} | {ff} | {fp} | {sf} | {df} | {mv} |")

    lines.append(f"")

    # --- Geometry table ---
    lines.append(f"## Zone Geometry Detail")
    lines.append(f"")
    lines.append("| Zone | Floor Area (m2) | Volume (m3) | Ceiling Ht (m) | Ext Wall (m2) | Ext Window (m2) | Ext Roof (m2) |")
    lines.append("|------|----------------|------------|----------------|--------------|----------------|--------------|")

    for d in shown:
        ch = f"{d['ceiling_height_m']:.1f}" if d['ceiling_height_m'] else "N/A"
        lines.append(f"| {d['zone']} | {d['floor_area_m2']:.1f} | {d['volume_m3']:.1f} | {ch} | {d['ext_wall_area_m2']:.1f} | {d['ext_window_area_m2']:.1f} | {d['ext_roof_area_m2']:.1f} |")

    lines.append(f"")

    # --- Flags / root cause indicators ---
    lines.append(f"## Root Cause Indicators")
    lines.append(f"")
    lines.append("| Zone | Occ Htg (hr) | Undersized? | High Envelope? | High Vent? | High Vol/Area? |")
    lines.append("|------|-------------|-------------|---------------|-----------|---------------|")

    for d in shown:
        undersized = "YES" if d['capacity_ratio'] is not None and d['capacity_ratio'] < 1.0 else "no"
        high_env = "YES" if d['envelope_exposure_ratio'] is not None and d['envelope_exposure_ratio'] > 1.5 else "no"
        high_vent = "YES" if d['vent_fraction'] is not None and d['vent_fraction'] > 0.3 else "no"
        high_vol = "YES" if d['volume_per_area'] is not None and d['volume_per_area'] > 5.0 else "no"
        lines.append(f"| {d['zone']} | {d['occ_htg_unmet_hr']:.1f} | {undersized} | {high_env} | {high_vent} | {high_vol} |")

    return "\n".join(lines)


def format_csv_output(data, threshold=0):
    """Format diagnostics as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        'Zone', 'Occ Htg Unmet (hr)', 'Occ Clg Unmet (hr)',
        'Tot Htg Unmet (hr)', 'Tot Clg Unmet (hr)',
        'Design Htg Load (W)', 'Design Air Flow (m3/s)',
        'Peak OAT (C)', 'Peak Date/Time',
        'Air Loop', 'Coil Name', 'Coil Capacity (W)', 'Capacity Ratio',
        'Fan Flow (m3/s)', 'Fan dP (Pa)', 'System Air Flow (m3/s)',
        'Floor Area (m2)', 'Volume (m3)', 'Ceiling Height (m)',
        'Ext Wall Area (m2)', 'Ext Window Area (m2)', 'Ext Roof Area (m2)',
        'Envelope Exposure Ratio', 'Volume/Area', 'Vent Fraction',
        'Htg Schedule', 'Voz (m3/s)', 'Avg OA (ach)', 'Avg OA (m3/s)',
    ]
    writer.writerow(headers)

    diags_sorted = sorted(data['diagnostics'],
                          key=lambda d: d['occ_htg_unmet_hr'], reverse=True)

    for d in diags_sorted:
        if d['occ_htg_unmet_hr'] <= threshold and d['occ_clg_unmet_hr'] <= threshold:
            continue
        writer.writerow([
            d['zone'], d['occ_htg_unmet_hr'], d['occ_clg_unmet_hr'],
            d['tot_htg_unmet_hr'], d['tot_clg_unmet_hr'],
            d['design_htg_load_w'], d['design_air_flow_m3s'],
            d['peak_oat_c'], d['peak_datetime'],
            d['airloop'], d['coil_name'], d['coil_capacity_w'], d['capacity_ratio'],
            d['fan_flow_m3s'], d['fan_pressure_pa'], d['system_air_flow_m3s'],
            d['floor_area_m2'], d['volume_m3'], d['ceiling_height_m'],
            d['ext_wall_area_m2'], d['ext_window_area_m2'], d['ext_roof_area_m2'],
            d['envelope_exposure_ratio'], d['volume_per_area'], d['vent_fraction'],
            d['htg_schedule'], d['voz_m3s'], d['mech_vent_ach'], d['avg_oa_m3s'],
        ])

    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(
        description='Per-zone diagnostic cross-reference for EnergyPlus unmet hours')
    parser.add_argument('run_dir', help='Path to EnergyPlus run directory')
    parser.add_argument('--format', choices=['markdown', 'json', 'csv'],
                        default='markdown', help='Output format (default: markdown)')
    parser.add_argument('--output', help='Write output to file')
    parser.add_argument('--threshold', type=float, default=0,
                        help='Only show zones with > N unmet hours (default: 0)')
    args = parser.parse_args()

    data = build_diagnostics(args.run_dir)

    if args.format == 'json':
        output = json.dumps(data, indent=2, default=str)
    elif args.format == 'csv':
        output = format_csv_output(data, args.threshold)
    else:
        output = format_markdown(data, args.threshold)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"Output written to {args.output}")
    else:
        print(output)


if __name__ == '__main__':
    main()
