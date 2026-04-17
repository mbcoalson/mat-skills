"""
capacity-gap-analysis.py -- Capacity gap analysis for EnergyPlus zone warmup/cooldown

For each zone, calculates the theoretical warmup (or cooldown) time needed given the
setback temperature, occupied setpoint, zone thermal capacitance, and available heating
(or cooling) capacity. Zones where the theoretical warmup time exceeds the pre-start
time are structurally unable to meet setpoint by occupancy start.

Key formula:
    warmup_time_hours = (zone_volume * air_density * Cp * delta_T) / (net_capacity_W * 3600)

Where:
    net_capacity = coil_capacity - envelope_loss_rate
    (ventilation loss = 0 during pre-start by default)

This is a simplified steady-state estimate. The actual EnergyPlus calculation includes
thermal mass, solar gains, and dynamic effects. But this gives a first-order answer to
"is it physically possible to recover from setback in the allotted pre-start time?"

Usage:
    python capacity-gap-analysis.py <run-dir>
    python capacity-gap-analysis.py <run-dir> --setback 18.3 --setpoint 21.1
    python capacity-gap-analysis.py <run-dir> --prestart-hours 3.0
    python capacity-gap-analysis.py <run-dir> --altitude 1525
    python capacity-gap-analysis.py <run-dir> --mode cooling --setback 26.7 --setpoint 23.9
    python capacity-gap-analysis.py <run-dir> --format json --output results.json
    python capacity-gap-analysis.py <run-dir> --oa-during-warmup
    python capacity-gap-analysis.py <run-dir> --threshold 50

Arguments:
    run-dir         Path to the EnergyPlus run directory containing eplusout.sql.
                    Accepts the run/ subdirectory itself or the parent workflow dir
                    (auto-detects run/eplusout.sql if needed).

Options:
    --setback TEMP      Setback (unoccupied) temperature in C (default: attempt extraction)
    --setpoint TEMP     Occupied setpoint temperature in C (default: attempt extraction)
    --prestart-hours H  Pre-start period in hours (default: 3.0)
    --altitude M        Site altitude in meters for air density (default: 0, sea level)
    --mode MODE         Analysis mode: heating or cooling (default: heating)
    --format FORMAT     Output format: markdown (default), json, csv
    --output FILE       Write output to file instead of stdout
    --threshold N       Only show zones with > N occupied unmet hours (default: 0)
    --oa-during-warmup  Include OA ventilation loss during warmup (default: false)

Engineering Calculations (per zone):
    1. Air density at altitude:  rho = 1.225 * (1 - 2.25577e-5 * altitude)^5.25588
    2. Specific heat of air:     Cp = 1005 J/(kg*K)
    3. Delta T:                  setpoint - setback (heating) or setback - setpoint (cooling)
    4. Air thermal energy:       Q_air = volume * rho * Cp * delta_T  [Joules]
    5. Coil capacity:            from EquipmentSummary Heating/Cooling Coils [W]
    6. Envelope loss rate:       from design heating load scaled by delta_T,
                                 or UA-based if U-factors available [W]
    7. Ventilation loss:         0 during pre-start (default), or rho*Cp*Voz*delta_T if flag set
    8. Net capacity:             coil_capacity - envelope_loss - ventilation_loss [W]
    9. Warmup time:              Q_air / (net_capacity * 3600)  [hours]
   10. Gap:                      warmup_time - prestart_hours  [hours]

Python: 3.12 (stdlib only - sqlite3, json, csv, sys, os, argparse, math, io)
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


def air_density_at_altitude(altitude_m):
    """Calculate air density at altitude using barometric formula.

    rho = 1.225 * (1 - 2.25577e-5 * altitude)^5.25588
    Returns kg/m3.
    """
    if altitude_m <= 0:
        return 1.225
    return 1.225 * (1 - 2.25577e-5 * altitude_m) ** 5.25588


# ---------------------------------------------------------------------------
# SQL query functions
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


def query_zone_info(conn):
    """Zone area, volume from InputVerificationandResultsSummary -> Zone Summary."""
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


def query_heating_coils(conn):
    """Heating coil data from EquipmentSummary -> Heating Coils."""
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


def query_cooling_coils(conn):
    """Cooling coil data from EquipmentSummary -> Cooling Coils."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'EquipmentSummary'
        AND TableName = 'Cooling Coils'
    """)
    coils = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in coils:
            coils[name] = {}
        coils[name][col_name.strip()] = value.strip() if value else ''
    return coils


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


def query_zone_sizing_cooling(conn):
    """Zone design cooling loads from HVACSizingSummary -> Zone Sensible Cooling."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'HVACSizingSummary'
        AND TableName = 'Zone Sensible Cooling'
    """)
    zones = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        if name not in zones:
            zones[name] = {}
        zones[name][col_name.strip()] = value.strip() if value else ''
    return zones


def query_envelope_opaque(conn):
    """Exterior opaque surfaces with U-factors from EnvelopeSummary -> Opaque Exterior."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'EnvelopeSummary'
        AND TableName = 'Opaque Exterior'
        AND ColumnName IN ('Zone', 'Net Area', 'Tilt', 'U-Factor with Film')
    """)
    surfaces = {}
    for row_name, col_name, value in c.fetchall():
        sname = row_name.strip().upper()
        if sname not in surfaces:
            surfaces[sname] = {}
        surfaces[sname][col_name.strip()] = value.strip() if value else ''
    return surfaces


def query_envelope_fenestration(conn):
    """Exterior fenestration with U-factors from EnvelopeSummary -> Exterior Fenestration."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'EnvelopeSummary'
        AND TableName = 'Exterior Fenestration'
        AND ColumnName IN ('Zone', 'Area of Multiplied Openings', 'Glass Area', 'U-Factor')
    """)
    surfaces = {}
    for row_name, col_name, value in c.fetchall():
        sname = row_name.strip().upper()
        if sname not in surfaces:
            surfaces[sname] = {}
        surfaces[sname][col_name.strip()] = value.strip() if value else ''
    return surfaces


def query_outdoor_air(conn):
    """Design zone outdoor airflow (Voz) from OutdoorAirDetails."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'OutdoorAirDetails'
        AND TableName = 'Mechanical Ventilation Parameters by Zone'
        AND ColumnName = 'Design Zone Outdoor Airflow - Voz'
    """)
    zones = {}
    for row_name, _col_name, value in c.fetchall():
        name = row_name.strip().upper()
        zones[name] = safe_float(value)
    return zones


def query_thermostat_schedules(conn):
    """Thermostat schedule data from SystemSummary -> Thermostat Schedules."""
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


def query_airloop_sizing(conn):
    """AirLoopHVAC design data from ComponentSizingSummary."""
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


# ---------------------------------------------------------------------------
# Envelope UA aggregation
# ---------------------------------------------------------------------------

def aggregate_envelope_ua(opaque_surfaces, fenestration_surfaces):
    """Aggregate UA values by zone from opaque and fenestration surfaces.

    Returns dict: zone_name -> {
        'wall_ua_w_per_k': float,
        'roof_ua_w_per_k': float,
        'window_ua_w_per_k': float,
        'total_ua_w_per_k': float,
        'wall_area_m2': float,
        'roof_area_m2': float,
        'window_area_m2': float,
    }
    """
    zone_data = {}

    def ensure_zone(z):
        if z not in zone_data:
            zone_data[z] = {
                'wall_ua_w_per_k': 0.0, 'roof_ua_w_per_k': 0.0,
                'window_ua_w_per_k': 0.0, 'total_ua_w_per_k': 0.0,
                'wall_area_m2': 0.0, 'roof_area_m2': 0.0, 'window_area_m2': 0.0,
            }

    # Opaque surfaces
    for _sname, sdata in opaque_surfaces.items():
        zone = sdata.get('Zone', '').upper()
        if not zone:
            continue
        ensure_zone(zone)
        area = safe_float(sdata.get('Net Area', '0'))
        u_factor = safe_float(sdata.get('U-Factor with Film', '0'))
        tilt = safe_float(sdata.get('Tilt', '90'))
        ua = u_factor * area
        if tilt > 45:
            zone_data[zone]['wall_ua_w_per_k'] += ua
            zone_data[zone]['wall_area_m2'] += area
        else:
            zone_data[zone]['roof_ua_w_per_k'] += ua
            zone_data[zone]['roof_area_m2'] += area
        zone_data[zone]['total_ua_w_per_k'] += ua

    # Fenestration
    for _sname, sdata in fenestration_surfaces.items():
        zone = sdata.get('Zone', '').upper()
        if not zone:
            continue
        ensure_zone(zone)
        area = safe_float(sdata.get('Area of Multiplied Openings',
                                     sdata.get('Glass Area', '0')))
        u_factor = safe_float(sdata.get('U-Factor', '0'))
        ua = u_factor * area
        zone_data[zone]['window_ua_w_per_k'] += ua
        zone_data[zone]['window_area_m2'] += area
        zone_data[zone]['total_ua_w_per_k'] += ua

    return zone_data


# ---------------------------------------------------------------------------
# Zone-to-equipment matching
# ---------------------------------------------------------------------------

def match_zone_to_airloop(zone_name, airloop_names):
    """Match a zone name to its serving air loop.

    Strategy 1: PSZ-AC <ZONE_NAME> exact match
    Strategy 2: Zone name embedded in air loop name
    Strategy 3: Partial word overlap (>=60%)
    Returns (airloop_name, match_method) or (None, None).
    """
    zone_upper = zone_name.upper()

    psz_name = f"PSZ-AC {zone_upper}"
    if psz_name in airloop_names:
        return psz_name, "PSZ-AC"

    for loop in airloop_names:
        if zone_upper in loop:
            return loop, "name-embed"

    zone_words = set(zone_upper.split())
    if not zone_words:
        return None, None
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


def match_zone_to_coil(zone_name, coil_names, airloop_name=None, mode='heating'):
    """Match a zone to its heating or cooling coil."""
    zone_upper = zone_name.upper()

    if airloop_name:
        for coil in coil_names:
            if airloop_name in coil:
                return coil, "via-airloop"

    if mode == 'heating':
        direct = f"PSZ-AC {zone_upper} GAS HTG COIL"
    else:
        direct = f"PSZ-AC {zone_upper} CLG COIL"
    if direct in coil_names:
        return direct, "PSZ-AC-coil"

    for coil in coil_names:
        if zone_upper in coil:
            return coil, "name-embed-coil"

    zone_words = set(zone_upper.split())
    if not zone_words:
        return None, None
    best_match = None
    best_score = 0
    for coil in coil_names:
        coil_words = set(coil.split())
        overlap = len(zone_words & coil_words)
        if overlap > best_score and overlap >= len(zone_words) * 0.6:
            best_score = overlap
            best_match = coil
    if best_match:
        return best_match, "partial-coil"

    return None, None


# ---------------------------------------------------------------------------
# Setback/setpoint extraction from thermostat schedules
# ---------------------------------------------------------------------------

def extract_setback_setpoint(thermostat_data, mode='heating'):
    """Attempt to extract setback and setpoint temperatures from thermostat schedule data.

    Returns (setback, setpoint, ambiguous: bool, warning_msg: str or None)
    """
    all_temps = []
    schedule_key = 'Heating' if mode == 'heating' else 'Cooling'

    for zone, data in thermostat_data.items():
        for col_name, value in data.items():
            if schedule_key.lower() in col_name.lower() and 'temp' in col_name.lower():
                parts = value.replace(',', ' ').split()
                for p in parts:
                    t = safe_float(p, default=None)
                    if t is not None and -50 < t < 80:
                        all_temps.append(t)

    if not all_temps:
        return None, None, True, "No thermostat temperature data found in SQL"

    unique_temps = sorted(set(all_temps))

    if mode == 'heating':
        if len(unique_temps) == 1:
            return None, unique_temps[0], True, (
                f"Only one heating temperature found ({unique_temps[0]} C). "
                "Cannot distinguish setback from setpoint. Use --setback and --setpoint."
            )
        elif len(unique_temps) == 2:
            return unique_temps[0], unique_temps[1], False, None
        else:
            return unique_temps[0], unique_temps[-1], True, (
                f"Multiple heating temperatures found: {unique_temps}. "
                f"Using min={unique_temps[0]} C as setback and max={unique_temps[-1]} C as setpoint. "
                "Verify with --setback and --setpoint if incorrect."
            )
    else:
        if len(unique_temps) == 1:
            return unique_temps[0], None, True, (
                f"Only one cooling temperature found ({unique_temps[0]} C). "
                "Cannot distinguish setback from setpoint. Use --setback and --setpoint."
            )
        elif len(unique_temps) == 2:
            return unique_temps[1], unique_temps[0], False, None
        else:
            return unique_temps[-1], unique_temps[0], True, (
                f"Multiple cooling temperatures found: {unique_temps}. "
                f"Using max={unique_temps[-1]} C as setback and min={unique_temps[0]} C as setpoint. "
                "Verify with --setback and --setpoint if incorrect."
            )


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

CP_AIR = 1005.0  # J/(kg*K) - specific heat of air at constant pressure


def build_capacity_gap_analysis(run_dir, setback_c=None, setpoint_c=None,
                                 prestart_hr=3.0, altitude_m=0.0,
                                 mode='heating', oa_during_warmup=False):
    """Build the complete per-zone capacity gap analysis dataset."""
    sql_path = find_sql(run_dir)
    conn = sqlite3.connect(sql_path)

    warnings = []

    # Gather all data
    zone_unmet = query_zone_unmet(conn)
    zone_info = query_zone_info(conn)
    heating_coils = query_heating_coils(conn)
    cooling_coils = query_cooling_coils(conn)
    zone_sizing_htg = query_zone_sizing_heating(conn)
    zone_sizing_clg = query_zone_sizing_cooling(conn)
    opaque_surfaces = query_envelope_opaque(conn)
    fenestration_surfaces = query_envelope_fenestration(conn)
    outdoor_air = query_outdoor_air(conn)
    thermostat_data = query_thermostat_schedules(conn)
    airloop_sizing = query_airloop_sizing(conn)

    conn.close()

    # Setback / setpoint resolution
    extracted_setback = None
    extracted_setpoint = None

    if setback_c is None or setpoint_c is None:
        ext_sb, ext_sp, ambiguous, warn_msg = extract_setback_setpoint(thermostat_data, mode)
        if warn_msg:
            warnings.append(warn_msg)
        extracted_setback = ext_sb
        extracted_setpoint = ext_sp

    if setback_c is None:
        if extracted_setback is not None:
            setback_c = extracted_setback
        else:
            setback_c = 18.3 if mode == 'heating' else 26.7
            warnings.append(
                f"Could not extract setback from SQL. Using default: {setback_c} C. "
                "Override with --setback."
            )

    if setpoint_c is None:
        if extracted_setpoint is not None:
            setpoint_c = extracted_setpoint
        else:
            setpoint_c = 21.1 if mode == 'heating' else 23.9
            warnings.append(
                f"Could not extract setpoint from SQL. Using default: {setpoint_c} C. "
                "Override with --setpoint."
            )

    # Computed parameters
    rho = air_density_at_altitude(altitude_m)
    if mode == 'heating':
        delta_t = setpoint_c - setback_c
    else:
        delta_t = setback_c - setpoint_c

    if delta_t <= 0:
        warnings.append(
            f"WARNING: delta_T is {delta_t:.2f} C (non-positive). Check setback/setpoint values. "
            f"For {mode} mode: setback={setback_c}, setpoint={setpoint_c}."
        )
        delta_t = max(delta_t, 0.1)

    # Envelope UA aggregation
    envelope_ua = aggregate_envelope_ua(opaque_surfaces, fenestration_surfaces)

    # Equipment name sets for matching
    airloop_names = set(airloop_sizing.keys())
    if mode == 'heating':
        coil_data = heating_coils
    else:
        coil_data = cooling_coils
    coil_names = set(coil_data.keys())

    zone_sizing = zone_sizing_htg if mode == 'heating' else zone_sizing_clg

    # Union of all zones
    all_zones = sorted(
        set(zone_unmet.keys()) |
        set(zone_info.keys()) |
        set(zone_sizing.keys())
    )

    # Per-zone analysis
    zone_results = []

    for zone in all_zones:
        unmet = zone_unmet.get(zone, {})
        info = zone_info.get(zone, {})
        sizing = zone_sizing.get(zone, {})
        env = envelope_ua.get(zone, {})
        voz_m3s = outdoor_air.get(zone, 0.0)

        if mode == 'heating':
            occ_unmet_hr = unmet.get('During Occupied Heating', 0.0)
        else:
            occ_unmet_hr = unmet.get('During Occupied Cooling', 0.0)

        volume_m3 = safe_float(info.get('Volume', '0'))
        floor_area_m2 = safe_float(info.get('Area', '0'))

        if volume_m3 <= 0:
            continue

        # Equipment matching
        airloop_name, _ = match_zone_to_airloop(zone, airloop_names)
        coil_name, _ = match_zone_to_coil(zone, coil_names, airloop_name, mode)

        # Coil capacity (W)
        coil_capacity_w = None
        if coil_name and coil_name in coil_data:
            cd = coil_data[coil_name]
            cap_str = cd.get('Nominal Total Capacity',
                             cd.get('Nominal Capacity', '0'))
            coil_capacity_w = safe_float(cap_str)

        # Envelope loss estimate (W)
        total_ua = env.get('total_ua_w_per_k', 0.0)
        peak_oat_str = sizing.get('Outdoor Temperature at Peak Load', '')
        peak_oat_c = safe_float(peak_oat_str, default=None)
        design_load_w = safe_float(sizing.get('Calculated Design Load', '0'))

        envelope_loss_w = 0.0
        envelope_loss_method = 'none'

        if total_ua > 0 and peak_oat_c is not None:
            if mode == 'heating':
                avg_indoor_during_warmup = (setback_c + setpoint_c) / 2.0
                dt_outdoor = avg_indoor_during_warmup - peak_oat_c
                if dt_outdoor > 0:
                    envelope_loss_w = total_ua * dt_outdoor
            else:
                avg_indoor_during_warmup = (setback_c + setpoint_c) / 2.0
                dt_outdoor = peak_oat_c - avg_indoor_during_warmup
                if dt_outdoor > 0:
                    envelope_loss_w = total_ua * dt_outdoor
            envelope_loss_method = 'UA-based'
        elif design_load_w > 0 and peak_oat_c is not None:
            if mode == 'heating':
                design_delta_t = setpoint_c - peak_oat_c
                if design_delta_t > 0:
                    avg_indoor = (setback_c + setpoint_c) / 2.0
                    warmup_dt = avg_indoor - peak_oat_c
                    if warmup_dt > 0:
                        scale = warmup_dt / design_delta_t
                        envelope_loss_w = design_load_w * scale
            else:
                design_delta_t = peak_oat_c - setpoint_c
                if design_delta_t > 0:
                    avg_indoor = (setback_c + setpoint_c) / 2.0
                    warmup_dt = peak_oat_c - avg_indoor
                    if warmup_dt > 0:
                        scale = warmup_dt / design_delta_t
                        envelope_loss_w = design_load_w * scale
            envelope_loss_method = 'design-load-scaled'
        elif design_load_w > 0:
            envelope_loss_w = design_load_w
            envelope_loss_method = 'design-load-direct'

        # Ventilation loss during warmup (W)
        ventilation_loss_w = 0.0
        if oa_during_warmup and voz_m3s > 0:
            ventilation_loss_w = rho * CP_AIR * voz_m3s * delta_t

        # Air thermal energy to recover (J)
        q_air_j = volume_m3 * rho * CP_AIR * delta_t

        # Net heating/cooling capacity (W)
        net_capacity_w = None
        if coil_capacity_w is not None and coil_capacity_w > 0:
            net_capacity_w = coil_capacity_w - envelope_loss_w - ventilation_loss_w

        # Warmup time (hours)
        warmup_hr = None
        if net_capacity_w is not None and net_capacity_w > 0:
            warmup_hr = q_air_j / (net_capacity_w * 3600.0)
        elif net_capacity_w is not None and net_capacity_w <= 0:
            warmup_hr = float('inf')

        # Gap
        gap_hr = None
        can_recover = None
        if warmup_hr is not None:
            if warmup_hr == float('inf'):
                gap_hr = float('inf')
                can_recover = False
            else:
                gap_hr = warmup_hr - prestart_hr
                can_recover = gap_hr <= 0

        zone_results.append({
            'zone': zone,
            'occ_unmet_hr': round(occ_unmet_hr, 2),
            'volume_m3': round(volume_m3, 2),
            'floor_area_m2': round(floor_area_m2, 2),
            'coil_name': coil_name or 'N/A',
            'coil_capacity_w': round(coil_capacity_w, 1) if coil_capacity_w is not None else None,
            'envelope_loss_w': round(envelope_loss_w, 1),
            'envelope_loss_method': envelope_loss_method,
            'ventilation_loss_w': round(ventilation_loss_w, 1),
            'voz_m3s': round(voz_m3s, 5),
            'net_capacity_w': round(net_capacity_w, 1) if net_capacity_w is not None else None,
            'q_air_j': round(q_air_j, 1),
            'warmup_hr': round(warmup_hr, 4) if warmup_hr is not None and warmup_hr != float('inf') else warmup_hr,
            'prestart_hr': prestart_hr,
            'gap_hr': round(gap_hr, 4) if gap_hr is not None and gap_hr != float('inf') else gap_hr,
            'can_recover': can_recover,
            'design_load_w': round(design_load_w, 1),
            'peak_oat_c': round(peak_oat_c, 2) if peak_oat_c is not None else None,
            'total_ua_w_per_k': round(total_ua, 2) if total_ua else 0.0,
            'wall_area_m2': round(env.get('wall_area_m2', 0.0), 2),
            'roof_area_m2': round(env.get('roof_area_m2', 0.0), 2),
            'window_area_m2': round(env.get('window_area_m2', 0.0), 2),
            'airloop': airloop_name or 'N/A',
        })

    # Sort by unmet hours descending, then by gap descending
    zone_results.sort(key=lambda z: (
        -(z['occ_unmet_hr'] or 0),
        -(z['gap_hr'] if z['gap_hr'] is not None and z['gap_hr'] != float('inf') else 99999),
    ))

    # Summary
    zones_can_recover = sum(1 for z in zone_results if z['can_recover'] is True)
    zones_cannot_recover = sum(1 for z in zone_results if z['can_recover'] is False)
    zones_unknown = sum(1 for z in zone_results if z['can_recover'] is None)
    structurally_limited = [
        z['zone'] for z in zone_results if z['can_recover'] is False
    ]

    parameters = {
        'setback_c': setback_c,
        'setpoint_c': setpoint_c,
        'delta_t_c': round(delta_t, 2),
        'prestart_hr': prestart_hr,
        'altitude_m': altitude_m,
        'air_density_kg_m3': round(rho, 4),
        'mode': mode,
        'oa_during_warmup': oa_during_warmup,
        'cp_air_j_per_kg_k': CP_AIR,
    }

    summary = {
        'total_zones_analyzed': len(zone_results),
        'zones_can_recover': zones_can_recover,
        'zones_cannot_recover': zones_cannot_recover,
        'zones_no_equipment_match': zones_unknown,
        'structurally_limited_zones': structurally_limited,
    }

    return {
        'run_dir': os.path.abspath(run_dir),
        'sql_path': os.path.abspath(sql_path),
        'parameters': parameters,
        'zones': zone_results,
        'summary': summary,
        'warnings': warnings,
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _fmt_val(value, fmt='.1f', na='N/A'):
    """Format a numeric value, returning na string for None or inf."""
    if value is None:
        return na
    if isinstance(value, float) and math.isinf(value):
        return 'INF'
    try:
        return f"{value:{fmt}}"
    except (ValueError, TypeError):
        return na


def format_markdown(data, threshold=0):
    """Format capacity gap analysis as markdown."""
    lines = []
    params = data['parameters']
    zones = data['zones']
    summary = data['summary']
    warnings = data['warnings']

    mode_label = 'Heating' if params['mode'] == 'heating' else 'Cooling'
    unmet_label = f'Occ {mode_label[:3]} (hr)'

    lines.append(f"# Capacity Gap Analysis Report ({mode_label} Mode)")
    lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## Analysis Parameters")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|-----------|-------|")
    lines.append(f"| Mode | {mode_label} |")
    lines.append(f"| Setback temperature | {params['setback_c']:.1f} C |")
    lines.append(f"| Occupied setpoint | {params['setpoint_c']:.1f} C |")
    lines.append(f"| Delta T | {params['delta_t_c']:.2f} C |")
    lines.append(f"| Pre-start period | {params['prestart_hr']:.1f} hr |")
    lines.append(f"| Altitude | {params['altitude_m']:.0f} m |")
    lines.append(f"| Air density | {params['air_density_kg_m3']:.4f} kg/m3 |")
    lines.append(f"| Cp (air) | {params['cp_air_j_per_kg_k']:.0f} J/(kg*K) |")
    lines.append(f"| OA during warmup | {'Yes' if params['oa_during_warmup'] else 'No'} |")
    lines.append("")

    shown = [z for z in zones if z['occ_unmet_hr'] > threshold or z['can_recover'] is False]

    lines.append("## Zone Capacity Gap Table")
    lines.append("")
    lines.append(f"**Zones analyzed:** {summary['total_zones_analyzed']}  ")
    lines.append(f"**Zones shown:** {len(shown)} (threshold: >{threshold} hr unmet or structurally limited)")
    lines.append("")

    lines.append(
        f"| Zone | {unmet_label} | Vol (m3) | Coil Cap (kW) | Env Loss (kW) "
        f"| Net Cap (kW) | Q_air (MJ) | Warmup (hr) | Pre-start (hr) | Gap (hr) | Can Recover? |"
    )
    lines.append("|------|" + "------|" * 10)

    for z in shown:
        coil_kw = _fmt_val(safe_div(z['coil_capacity_w'], 1000.0) if z['coil_capacity_w'] is not None else None)
        env_kw = _fmt_val(z['envelope_loss_w'] / 1000.0 if z['envelope_loss_w'] else 0.0)
        net_kw = _fmt_val(safe_div(z['net_capacity_w'], 1000.0) if z['net_capacity_w'] is not None else None)
        q_mj = _fmt_val(z['q_air_j'] / 1e6, fmt='.3f') if z['q_air_j'] else 'N/A'
        warmup = _fmt_val(z['warmup_hr'], fmt='.3f')
        gap = _fmt_val(z['gap_hr'], fmt='.3f')
        recover = 'Yes' if z['can_recover'] is True else ('NO' if z['can_recover'] is False else 'N/A')

        lines.append(
            f"| {z['zone']} | {z['occ_unmet_hr']:.1f} | {z['volume_m3']:.1f} "
            f"| {coil_kw} | {env_kw} | {net_kw} | {q_mj} "
            f"| {warmup} | {z['prestart_hr']:.1f} | {gap} | {recover} |"
        )

    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Total zones analyzed | {summary['total_zones_analyzed']} |")
    lines.append(f"| Zones that CAN recover in pre-start time | {summary['zones_can_recover']} |")
    lines.append(f"| Zones that CANNOT recover (structurally limited) | {summary['zones_cannot_recover']} |")
    lines.append(f"| Zones with no equipment match (unknown) | {summary['zones_no_equipment_match']} |")
    lines.append("")

    if summary['structurally_limited_zones']:
        lines.append("### Structurally Limited Zones")
        lines.append("")
        lines.append("These zones cannot physically recover from setback to setpoint within the "
                      f"pre-start period ({params['prestart_hr']:.1f} hr):")
        lines.append("")
        for zname in summary['structurally_limited_zones']:
            zdata = next((z for z in zones if z['zone'] == zname), None)
            if zdata:
                warmup_str = _fmt_val(zdata['warmup_hr'], fmt='.3f')
                gap_str = _fmt_val(zdata['gap_hr'], fmt='.3f')
                lines.append(f"- **{zname}**: warmup={warmup_str} hr, gap={gap_str} hr")
            else:
                lines.append(f"- **{zname}**")
        lines.append("")

    lines.append("## Engineering Notes")
    lines.append("")
    lines.append("- **Warmup time** is a simplified steady-state estimate: "
                 "`Q_air / (net_capacity * 3600)` where `Q_air = V * rho * Cp * delta_T`.")
    lines.append("- **Envelope loss** is estimated from UA values (if available) or "
                 "scaled from design heating load. The average indoor temperature during "
                 "warmup is used: `(setback + setpoint) / 2`.")
    lines.append("- **Thermal mass** of walls, floors, and furnishings is NOT included. "
                 "Actual warmup will be longer than calculated here.")
    lines.append("- **Net capacity** = coil capacity - envelope loss - ventilation loss. "
                 "Negative net capacity means the coil cannot overcome losses even at steady state.")
    lines.append("- Zones showing 'N/A' for recovery could not be matched to HVAC equipment.")

    return "\n".join(lines)


def format_json(data):
    """Format capacity gap analysis as JSON."""

    def _serialize(obj):
        if isinstance(obj, float) and math.isinf(obj):
            return "Infinity"
        return str(obj)

    return json.dumps(data, indent=2, default=_serialize)


def format_csv_output(data, threshold=0):
    """Format capacity gap analysis as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)

    params = data['parameters']
    mode_label = 'Heating' if params['mode'] == 'heating' else 'Cooling'

    headers = [
        'Zone', f'Occ {mode_label[:3]} Unmet (hr)', 'Volume (m3)',
        'Floor Area (m2)', 'Coil Name', 'Coil Capacity (W)',
        'Envelope Loss (W)', 'Envelope Loss Method', 'Ventilation Loss (W)',
        'Net Capacity (W)', 'Q_air (J)', 'Warmup Time (hr)',
        'Pre-start (hr)', 'Gap (hr)', 'Can Recover',
        'Design Load (W)', 'Peak OAT (C)', 'Total UA (W/K)',
        'Wall Area (m2)', 'Roof Area (m2)', 'Window Area (m2)',
        'Voz (m3/s)', 'Air Loop',
    ]
    writer.writerow(headers)

    for z in data['zones']:
        if z['occ_unmet_hr'] <= threshold and z['can_recover'] is not False:
            continue

        gap_val = z['gap_hr']
        if isinstance(gap_val, float) and math.isinf(gap_val):
            gap_val = 'Infinity'
        warmup_val = z['warmup_hr']
        if isinstance(warmup_val, float) and math.isinf(warmup_val):
            warmup_val = 'Infinity'

        writer.writerow([
            z['zone'], z['occ_unmet_hr'], z['volume_m3'], z['floor_area_m2'],
            z['coil_name'], z['coil_capacity_w'], z['envelope_loss_w'],
            z['envelope_loss_method'], z['ventilation_loss_w'],
            z['net_capacity_w'], z['q_air_j'], warmup_val,
            z['prestart_hr'], gap_val, z['can_recover'],
            z['design_load_w'], z['peak_oat_c'], z['total_ua_w_per_k'],
            z['wall_area_m2'], z['roof_area_m2'], z['window_area_m2'],
            z['voz_m3s'], z['airloop'],
        ])

    return output.getvalue()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            'Capacity gap analysis for EnergyPlus zone warmup/cooldown. '
            'Calculates whether each zone can physically recover from setback '
            'to occupied setpoint within the pre-start period.'
        )
    )
    parser.add_argument('run_dir',
                        help='Path to EnergyPlus run directory containing eplusout.sql')
    parser.add_argument('--setback', type=float, default=None,
                        help='Setback (unoccupied) temperature in C (default: auto-detect)')
    parser.add_argument('--setpoint', type=float, default=None,
                        help='Occupied setpoint temperature in C (default: auto-detect)')
    parser.add_argument('--prestart-hours', type=float, default=3.0,
                        help='Pre-start period in hours (default: 3.0)')
    parser.add_argument('--altitude', type=float, default=0.0,
                        help='Site altitude in meters for air density (default: 0)')
    parser.add_argument('--mode', choices=['heating', 'cooling'], default='heating',
                        help='Analysis mode: heating or cooling (default: heating)')
    parser.add_argument('--format', choices=['markdown', 'json', 'csv'],
                        default='markdown', help='Output format (default: markdown)')
    parser.add_argument('--output', help='Write output to file instead of stdout')
    parser.add_argument('--threshold', type=float, default=0,
                        help='Only show zones with > N occupied unmet hours (default: 0)')
    parser.add_argument('--oa-during-warmup', action='store_true', default=False,
                        help='Include OA ventilation loss during warmup (default: false)')

    args = parser.parse_args()

    data = build_capacity_gap_analysis(
        run_dir=args.run_dir,
        setback_c=args.setback,
        setpoint_c=args.setpoint,
        prestart_hr=args.prestart_hours,
        altitude_m=args.altitude,
        mode=args.mode,
        oa_during_warmup=args.oa_during_warmup,
    )

    for w in data.get('warnings', []):
        print(f"WARNING: {w}", file=sys.stderr)

    if args.format == 'json':
        output_text = format_json(data)
    elif args.format == 'csv':
        output_text = format_csv_output(data, args.threshold)
    else:
        output_text = format_markdown(data, args.threshold)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output_text)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output_text)


if __name__ == '__main__':
    main()
