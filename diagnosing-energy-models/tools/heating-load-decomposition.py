"""
heating-load-decomposition.py — Peak heating load decomposition for EnergyPlus models

For each zone in any EnergyPlus model, decomposes the peak heating sensible heat gain
into its components using SensibleHeatGainSummary -> Peak Heating Sensible Heat Gain
Components. This identifies whether unmet hours are driven by envelope conduction,
infiltration, ventilation, internal gains, or thermal mass.

Cross-references with unmet hours to identify which load components dominate in the
worst zones.

Usage:
    python heating-load-decomposition.py <run-dir>
    python heating-load-decomposition.py <run-dir> --format markdown
    python heating-load-decomposition.py <run-dir> --format json
    python heating-load-decomposition.py <run-dir> --format csv
    python heating-load-decomposition.py <run-dir> --zones "STUDY 123,NATATORIUM 155"
    python heating-load-decomposition.py <run-dir> --output decomposition.md
    python heating-load-decomposition.py <run-dir> --threshold 50

Arguments:
    run-dir     Path to the EnergyPlus run directory containing eplusout.sql.
                Accepts the run/ subdirectory itself or the parent workflow dir
                (auto-detects run/eplusout.sql if needed).

Options:
    --format FORMAT   Output format: markdown (default), json, csv
    --output FILE     Write output to file instead of stdout
    --threshold N     Only show zones with > N occupied heating unmet hours (default: 0)
    --zones LIST      Comma-separated zone names to include (case-insensitive, default: all)

Per-Zone Decomposition Data:
    - Occupied heating unmet hours (cross-referenced from SystemSummary)
    - Peak heating timestamp
    - Envelope losses (opaque surface conduction + window heat removal)
    - Infiltration losses
    - HVAC heating delivery (terminal unit + zone eq + heated surface)
    - Internal gains (people + lights + equipment)
    - Interzone air transfer (net of addition and removal)
    - Loss category percentages

Python: 3.x (stdlib only - sqlite3, json, csv, sys, os, argparse, io)
"""

import sqlite3
import json
import csv
import sys
import os
import argparse
import io


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


def query_peak_heating_components(conn):
    """Peak heating sensible heat gain components from SensibleHeatGainSummary.

    Discovers columns dynamically to handle EnergyPlus version differences.
    Returns dict keyed by uppercase zone name, each value is a dict of column->float.
    """
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'SensibleHeatGainSummary'
        AND TableName = 'Peak Heating Sensible Heat Gain Components'
    """)
    zones = {}
    for row_name, col_name, value in c.fetchall():
        name = row_name.strip().upper()
        col = col_name.strip()
        if name not in zones:
            zones[name] = {}
        # Timestamp columns store strings; numeric columns store watts
        if 'Time of Peak' in col or 'TIMESTAMP' in col.upper():
            zones[name][col] = value.strip() if value else ''
        else:
            zones[name][col] = safe_float(value)
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


def discover_columns(peak_data):
    """Discover all unique column names present in the peak heating data.

    Returns a sorted list of column names (excluding timestamp columns).
    """
    all_cols = set()
    for zone_data in peak_data.values():
        for col in zone_data:
            all_cols.add(col)
    return sorted(all_cols)


# ---------------------------------------------------------------------------
# Engineering classification of heat gain/loss components
# ---------------------------------------------------------------------------

def classify_components(zone_data):
    """Classify peak heating components into engineering categories.

    Dynamically matches column names using substring matching to handle
    EnergyPlus version variations.

    Returns a dict with classified component sums and the peak timestamp.
    """
    # Extract peak timestamp
    peak_time = ''
    for col, val in zone_data.items():
        if 'Time of Peak' in col or 'TIMESTAMP' in col.upper():
            peak_time = val if isinstance(val, str) else str(val)
            break

    # Helper: get a single column value matching a substring
    def get_matching(substring, default=0.0):
        for col, val in zone_data.items():
            if isinstance(val, str):
                continue
            if substring.lower() in col.lower():
                return val
        return default

    # Envelope losses: opaque surface conduction removal + window heat removal
    opaque_removal = get_matching('opaque surface conduction and other heat removal')
    window_removal = get_matching('window heat removal')
    envelope_loss = opaque_removal + window_removal

    # Window loss broken out separately for reporting
    window_loss = window_removal

    # Infiltration losses
    infiltration_removal = get_matching('infiltration heat removal')
    infiltration_addition = get_matching('infiltration heat addition')
    infiltration_net = infiltration_addition + infiltration_removal

    # HVAC heating delivery
    hvac_terminal_heating = get_matching('hvac terminal unit sensible air heating')
    hvac_zone_eq_heating = get_matching('hvac zone eq & other sensible air heating')
    hvac_heated_surface = get_matching('hvac input heated surface heating')
    hvac_delivery = hvac_terminal_heating + hvac_zone_eq_heating + hvac_heated_surface

    # HVAC cooling (system may also be cooling at the same time in some zones)
    hvac_terminal_cooling = get_matching('hvac terminal unit sensible air cooling')
    hvac_zone_eq_cooling = get_matching('hvac zone eq & other sensible air cooling')
    hvac_cooled_surface = get_matching('hvac input cooled surface cooling')
    hvac_cooling = hvac_terminal_cooling + hvac_zone_eq_cooling + hvac_cooled_surface

    # Internal gains (heat additions from people, lights, equipment)
    people = get_matching('people sensible heat addition')
    lights = get_matching('lights sensible heat addition')
    equipment_add = get_matching('equipment sensible heat addition')
    equipment_remove = get_matching('equipment sensible heat removal')
    internal_gains = people + lights + equipment_add

    # Interzone air transfer (net)
    interzone_add = get_matching('interzone air transfer heat addition')
    interzone_remove = get_matching('interzone air transfer heat removal')
    interzone_net = interzone_add + interzone_remove

    # Opaque surface addition (solar gain through mass, etc.)
    opaque_addition = get_matching('opaque surface conduction and other heat addition')
    window_addition = get_matching('window heat addition')

    # Total losses (sum of all negative/removal components)
    all_removals = []
    for col, val in zone_data.items():
        if isinstance(val, str):
            continue
        if val < 0:
            all_removals.append(val)
    total_losses = sum(all_removals)  # This will be negative

    # Calculate loss percentages (what fraction of total losses each component is)
    loss_pcts = {}
    if total_losses != 0:
        loss_pcts['envelope_pct'] = round((envelope_loss / total_losses) * 100, 1)
        loss_pcts['infiltration_pct'] = round((infiltration_removal / total_losses) * 100, 1)
        loss_pcts['window_pct'] = round((window_removal / total_losses) * 100, 1)
        loss_pcts['interzone_removal_pct'] = round((interzone_remove / total_losses) * 100, 1) if interzone_remove < 0 else 0.0
        loss_pcts['equipment_removal_pct'] = round((equipment_remove / total_losses) * 100, 1) if equipment_remove < 0 else 0.0
    else:
        loss_pcts['envelope_pct'] = 0.0
        loss_pcts['infiltration_pct'] = 0.0
        loss_pcts['window_pct'] = 0.0
        loss_pcts['interzone_removal_pct'] = 0.0
        loss_pcts['equipment_removal_pct'] = 0.0

    return {
        'peak_time': peak_time,
        'envelope_loss_w': round(envelope_loss, 2),
        'opaque_loss_w': round(opaque_removal, 2),
        'window_loss_w': round(window_removal, 2),
        'infiltration_loss_w': round(infiltration_removal, 2),
        'infiltration_net_w': round(infiltration_net, 2),
        'hvac_delivery_w': round(hvac_delivery, 2),
        'hvac_cooling_w': round(hvac_cooling, 2),
        'internal_gains_w': round(internal_gains, 2),
        'people_w': round(people, 2),
        'lights_w': round(lights, 2),
        'equipment_add_w': round(equipment_add, 2),
        'equipment_remove_w': round(equipment_remove, 2),
        'interzone_net_w': round(interzone_net, 2),
        'interzone_add_w': round(interzone_add, 2),
        'interzone_remove_w': round(interzone_remove, 2),
        'opaque_addition_w': round(opaque_addition, 2),
        'window_addition_w': round(window_addition, 2),
        'total_losses_w': round(total_losses, 2),
        'loss_percentages': loss_pcts,
        'raw': {col: val for col, val in zone_data.items()},
    }


# ---------------------------------------------------------------------------
# Main analysis assembly
# ---------------------------------------------------------------------------

def build_decomposition(run_dir, zone_filter=None, threshold=0):
    """Build the complete per-zone heating load decomposition dataset."""
    sql_path = find_sql(run_dir)
    conn = sqlite3.connect(sql_path)

    zone_unmet = query_zone_unmet(conn)
    peak_heating = query_peak_heating_components(conn)
    zone_info = query_zone_info(conn)

    conn.close()

    all_columns = discover_columns(peak_heating)

    # Union of zones from peak heating data and unmet hours
    all_zones = sorted(
        set(peak_heating.keys()) | set(zone_unmet.keys())
    )

    # Apply zone filter if provided
    if zone_filter:
        filter_set = {z.strip().upper() for z in zone_filter}
        all_zones = [z for z in all_zones if z in filter_set]

    results = []
    for zone in all_zones:
        unmet = zone_unmet.get(zone, {})
        occ_htg = unmet.get('During Occupied Heating', 0.0)
        occ_clg = unmet.get('During Occupied Cooling', 0.0)

        if threshold > 0 and occ_htg <= threshold:
            continue

        peak_data = peak_heating.get(zone, {})
        info = zone_info.get(zone, {})

        if peak_data:
            components = classify_components(peak_data)
        else:
            components = {
                'peak_time': '',
                'envelope_loss_w': 0.0, 'opaque_loss_w': 0.0,
                'window_loss_w': 0.0, 'infiltration_loss_w': 0.0,
                'infiltration_net_w': 0.0, 'hvac_delivery_w': 0.0,
                'hvac_cooling_w': 0.0, 'internal_gains_w': 0.0,
                'people_w': 0.0, 'lights_w': 0.0,
                'equipment_add_w': 0.0, 'equipment_remove_w': 0.0,
                'interzone_net_w': 0.0, 'interzone_add_w': 0.0,
                'interzone_remove_w': 0.0, 'opaque_addition_w': 0.0,
                'window_addition_w': 0.0, 'total_losses_w': 0.0,
                'loss_percentages': {
                    'envelope_pct': 0.0, 'infiltration_pct': 0.0,
                    'window_pct': 0.0, 'interzone_removal_pct': 0.0,
                    'equipment_removal_pct': 0.0,
                },
                'raw': {},
            }

        floor_area = safe_float(info.get('Area', '0'))
        volume = safe_float(info.get('Volume', '0'))

        envelope_intensity = safe_div(abs(components['envelope_loss_w']), floor_area)
        infiltration_intensity = safe_div(abs(components['infiltration_loss_w']), floor_area)
        hvac_intensity = safe_div(components['hvac_delivery_w'], floor_area)

        results.append({
            'zone': zone,
            'occ_htg_unmet_hr': occ_htg,
            'occ_clg_unmet_hr': occ_clg,
            'peak_time': components['peak_time'],
            'floor_area_m2': floor_area,
            'volume_m3': volume,
            'components': {
                'envelope_loss_w': components['envelope_loss_w'],
                'opaque_loss_w': components['opaque_loss_w'],
                'window_loss_w': components['window_loss_w'],
                'infiltration_loss_w': components['infiltration_loss_w'],
                'hvac_delivery_w': components['hvac_delivery_w'],
                'hvac_cooling_w': components['hvac_cooling_w'],
                'internal_gains_w': components['internal_gains_w'],
                'people_w': components['people_w'],
                'lights_w': components['lights_w'],
                'equipment_add_w': components['equipment_add_w'],
                'interzone_net_w': components['interzone_net_w'],
                'total_losses_w': components['total_losses_w'],
            },
            'loss_percentages': components['loss_percentages'],
            'envelope_intensity_w_m2': round(envelope_intensity, 2) if envelope_intensity is not None else None,
            'infiltration_intensity_w_m2': round(infiltration_intensity, 2) if infiltration_intensity is not None else None,
            'hvac_intensity_w_m2': round(hvac_intensity, 2) if hvac_intensity is not None else None,
            'raw_columns': components.get('raw', {}),
        })

    results.sort(key=lambda d: (d['occ_htg_unmet_hr'], abs(d['components']['envelope_loss_w'])),
                 reverse=True)

    # Cross-zone loss category summary
    category_totals = {
        'envelope_loss_w': 0.0,
        'infiltration_loss_w': 0.0,
        'window_loss_w': 0.0,
        'interzone_net_w': 0.0,
        'internal_gains_w': 0.0,
        'hvac_delivery_w': 0.0,
    }
    for r in results:
        for key in category_totals:
            category_totals[key] += r['components'].get(key, 0.0)

    loss_categories = [
        ('Envelope (opaque + window)', category_totals['envelope_loss_w']),
        ('Infiltration', category_totals['infiltration_loss_w']),
        ('Window only', category_totals['window_loss_w']),
        ('Interzone transfer', category_totals['interzone_net_w']),
    ]
    loss_categories.sort(key=lambda x: x[1])

    return {
        'run_dir': os.path.abspath(run_dir),
        'sql_path': os.path.abspath(find_sql(run_dir)),
        'zone_count_total': len(set(peak_heating.keys()) | set(zone_unmet.keys())),
        'zone_count_shown': len(results),
        'threshold': threshold,
        'available_columns': all_columns,
        'zones': results,
        'category_totals': category_totals,
        'loss_category_ranking': loss_categories,
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_markdown(data):
    """Format decomposition as markdown tables."""
    lines = []
    zones = data['zones']

    lines.append("# Heating Load Decomposition Report")
    lines.append("")
    lines.append(f"**Run directory:** `{data['run_dir']}`")
    lines.append(f"**Zones analyzed:** {data['zone_count_total']}")
    lines.append(f"**Zones shown:** {data['zone_count_shown']} (threshold: >{data['threshold']} hr)")
    lines.append("")

    if not zones:
        lines.append("*No zones match the filter criteria.*")
        return "\n".join(lines)

    # Summary statistics
    total_occ_htg = sum(z['occ_htg_unmet_hr'] for z in zones)
    zones_envelope_dominant = sum(
        1 for z in zones
        if z['loss_percentages'].get('envelope_pct', 0) > 70
    )
    zones_infiltration_dominant = sum(
        1 for z in zones
        if z['loss_percentages'].get('infiltration_pct', 0) > 30
    )
    max_envelope_zone = max(zones, key=lambda z: abs(z['components']['envelope_loss_w']))
    max_infiltration_zone = max(zones, key=lambda z: abs(z['components']['infiltration_loss_w']))

    lines.append("## Summary Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total occupied heating unmet hours (shown zones) | {total_occ_htg:.1f} |")
    lines.append(f"| Zones where envelope > 70% of losses | {zones_envelope_dominant} |")
    lines.append(f"| Zones where infiltration > 30% of losses | {zones_infiltration_dominant} |")
    lines.append(f"| Worst envelope loss zone | {max_envelope_zone['zone']} ({max_envelope_zone['components']['envelope_loss_w']:.0f} W) |")
    lines.append(f"| Worst infiltration loss zone | {max_infiltration_zone['zone']} ({max_infiltration_zone['components']['infiltration_loss_w']:.0f} W) |")
    lines.append("")

    # Loss category ranking
    lines.append("## Loss Category Ranking (sum across shown zones)")
    lines.append("")
    lines.append("| Rank | Category | Total (W) |")
    lines.append("|------|----------|-----------|")
    for rank, (cat_name, cat_val) in enumerate(data['loss_category_ranking'], 1):
        lines.append(f"| {rank} | {cat_name} | {cat_val:,.0f} |")
    lines.append("")

    # Main decomposition table
    lines.append("## Per-Zone Heating Load Decomposition")
    lines.append("")
    lines.append("| Zone | Occ Htg (hr) | Peak Time | Envelope Loss (W) | Infiltration (W) | HVAC Delivery (W) | Internal Gains (W) | Interzone (W) | Envelope % | Infiltration % |")
    lines.append("|------|-------------|-----------|-------------------|-----------------|-------------------|-------------------|--------------|-----------|---------------|")

    for z in zones:
        c = z['components']
        pcts = z['loss_percentages']
        lines.append(
            f"| {z['zone']} "
            f"| {z['occ_htg_unmet_hr']:.1f} "
            f"| {z['peak_time']} "
            f"| {c['envelope_loss_w']:,.1f} "
            f"| {c['infiltration_loss_w']:,.1f} "
            f"| {c['hvac_delivery_w']:,.1f} "
            f"| {c['internal_gains_w']:,.1f} "
            f"| {c['interzone_net_w']:,.1f} "
            f"| {pcts.get('envelope_pct', 0):.1f} "
            f"| {pcts.get('infiltration_pct', 0):.1f} |"
        )

    lines.append("")

    # Detailed component breakdown
    lines.append("## Detailed Component Breakdown")
    lines.append("")
    lines.append("| Zone | Opaque Loss (W) | Window Loss (W) | Infiltration (W) | People (W) | Lights (W) | Equipment (W) | HVAC Htg (W) | HVAC Clg (W) | Total Losses (W) |")
    lines.append("|------|----------------|----------------|-----------------|-----------|-----------|-------------|-------------|-------------|-----------------|")

    for z in zones:
        c = z['components']
        lines.append(
            f"| {z['zone']} "
            f"| {c['opaque_loss_w']:,.1f} "
            f"| {c['window_loss_w']:,.1f} "
            f"| {c['infiltration_loss_w']:,.1f} "
            f"| {c['people_w']:,.1f} "
            f"| {c['lights_w']:,.1f} "
            f"| {c['equipment_add_w']:,.1f} "
            f"| {c['hvac_delivery_w']:,.1f} "
            f"| {c['hvac_cooling_w']:,.1f} "
            f"| {c['total_losses_w']:,.1f} |"
        )

    lines.append("")

    # Intensity table
    lines.append("## Peak Load Intensity (W/m2)")
    lines.append("")
    lines.append("| Zone | Floor Area (m2) | Envelope (W/m2) | Infiltration (W/m2) | HVAC Delivery (W/m2) |")
    lines.append("|------|----------------|----------------|-------------------|---------------------|")

    for z in zones:
        env_i = f"{z['envelope_intensity_w_m2']:.1f}" if z['envelope_intensity_w_m2'] is not None else "N/A"
        inf_i = f"{z['infiltration_intensity_w_m2']:.1f}" if z['infiltration_intensity_w_m2'] is not None else "N/A"
        hvac_i = f"{z['hvac_intensity_w_m2']:.1f}" if z['hvac_intensity_w_m2'] is not None else "N/A"
        lines.append(
            f"| {z['zone']} "
            f"| {z['floor_area_m2']:.1f} "
            f"| {env_i} "
            f"| {inf_i} "
            f"| {hvac_i} |"
        )

    lines.append("")

    # Root cause flags
    lines.append("## Root Cause Indicators")
    lines.append("")
    lines.append("| Zone | Occ Htg (hr) | Envelope Dominant? | Infiltration Significant? | Low Internal Gains? | Interzone Losses? |")
    lines.append("|------|-------------|-------------------|--------------------------|--------------------|--------------------|")

    for z in zones:
        pcts = z['loss_percentages']
        env_dom = "YES" if pcts.get('envelope_pct', 0) > 70 else "no"
        inf_sig = "YES" if pcts.get('infiltration_pct', 0) > 15 else "no"
        low_ig = "YES" if z['components']['internal_gains_w'] < 50 else "no"
        iz_loss = "YES" if z['components']['interzone_net_w'] < -100 else "no"
        lines.append(
            f"| {z['zone']} "
            f"| {z['occ_htg_unmet_hr']:.1f} "
            f"| {env_dom} "
            f"| {inf_sig} "
            f"| {low_ig} "
            f"| {iz_loss} |"
        )

    return "\n".join(lines)


def format_json(data):
    """Format decomposition as JSON."""
    output = {
        'run_dir': data['run_dir'],
        'zone_count_total': data['zone_count_total'],
        'zone_count_shown': data['zone_count_shown'],
        'threshold': data['threshold'],
        'available_columns': data['available_columns'],
        'category_totals': data['category_totals'],
        'loss_category_ranking': [
            {'category': name, 'total_w': val}
            for name, val in data['loss_category_ranking']
        ],
        'zones': [],
    }

    for z in data['zones']:
        zone_out = {
            'zone': z['zone'],
            'occ_htg_unmet_hr': z['occ_htg_unmet_hr'],
            'occ_clg_unmet_hr': z['occ_clg_unmet_hr'],
            'peak_time': z['peak_time'],
            'floor_area_m2': z['floor_area_m2'],
            'volume_m3': z['volume_m3'],
            'components': z['components'],
            'loss_percentages': z['loss_percentages'],
            'envelope_intensity_w_m2': z['envelope_intensity_w_m2'],
            'infiltration_intensity_w_m2': z['infiltration_intensity_w_m2'],
            'hvac_intensity_w_m2': z['hvac_intensity_w_m2'],
        }
        output['zones'].append(zone_out)

    return json.dumps(output, indent=2, default=str)


def format_csv_output(data):
    """Format decomposition as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        'Zone', 'Occ Htg Unmet (hr)', 'Occ Clg Unmet (hr)', 'Peak Time',
        'Floor Area (m2)', 'Volume (m3)',
        'Envelope Loss (W)', 'Opaque Loss (W)', 'Window Loss (W)',
        'Infiltration Loss (W)', 'HVAC Delivery (W)', 'HVAC Cooling (W)',
        'Internal Gains (W)', 'People (W)', 'Lights (W)', 'Equipment (W)',
        'Interzone Net (W)', 'Total Losses (W)',
        'Envelope %', 'Infiltration %', 'Window %',
        'Envelope Intensity (W/m2)', 'Infiltration Intensity (W/m2)',
        'HVAC Intensity (W/m2)',
    ]
    writer.writerow(headers)

    for z in data['zones']:
        c = z['components']
        pcts = z['loss_percentages']
        writer.writerow([
            z['zone'], z['occ_htg_unmet_hr'], z['occ_clg_unmet_hr'],
            z['peak_time'], z['floor_area_m2'], z['volume_m3'],
            c['envelope_loss_w'], c['opaque_loss_w'], c['window_loss_w'],
            c['infiltration_loss_w'], c['hvac_delivery_w'], c['hvac_cooling_w'],
            c['internal_gains_w'], c['people_w'], c['lights_w'],
            c['equipment_add_w'], c['interzone_net_w'], c['total_losses_w'],
            pcts.get('envelope_pct', 0), pcts.get('infiltration_pct', 0),
            pcts.get('window_pct', 0),
            z['envelope_intensity_w_m2'], z['infiltration_intensity_w_m2'],
            z['hvac_intensity_w_m2'],
        ])

    return output.getvalue()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Decompose peak heating sensible heat gains by zone from EnergyPlus SQL output')
    parser.add_argument('run_dir', help='Path to EnergyPlus run directory')
    parser.add_argument('--format', choices=['markdown', 'json', 'csv'],
                        default='markdown', help='Output format (default: markdown)')
    parser.add_argument('--output', help='Write output to file instead of stdout')
    parser.add_argument('--threshold', type=float, default=0,
                        help='Only show zones with > N occupied heating unmet hours (default: 0)')
    parser.add_argument('--zones', type=str, default=None,
                        help='Comma-separated zone names to include (case-insensitive, default: all)')
    args = parser.parse_args()

    zone_filter = None
    if args.zones:
        zone_filter = [z.strip().upper() for z in args.zones.split(',')]

    data = build_decomposition(args.run_dir, zone_filter=zone_filter, threshold=args.threshold)

    if args.format == 'json':
        output = format_json(data)
    elif args.format == 'csv':
        output = format_csv_output(data)
    else:
        output = format_markdown(data)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"Output written to {args.output}")
    else:
        print(output)


if __name__ == '__main__':
    main()
