"""
extract-simulation-results.py — Deterministic EnergyPlus results extraction

Queries eplusout.sql and eplusout.err from a completed simulation run directory
and produces structured JSON + formatted markdown with all standard metrics.

Usage:
    python extract-simulation-results.py <run-dir>
    python extract-simulation-results.py <run-dir> --format json
    python extract-simulation-results.py <run-dir> --format markdown
    python extract-simulation-results.py <run-dir> --format both  (default)
    python extract-simulation-results.py <run-dir> --output results.json

Arguments:
    run-dir     Path to the EnergyPlus run directory containing eplusout.sql
                Can be the 'run/' subdirectory itself or the parent workflow dir
                (will auto-detect run/eplusout.sql if needed)

Output Metrics:
    - Facility occupied heating/cooling unmet hours
    - Zone-by-zone occupied heating unmet hours (all zones, sorted descending)
    - End-use energy breakdown by fuel type (GJ)
    - Total site energy, source energy, site EUI
    - Severe error count
    - Building area (ft² and m²)

Python: 3.x (stdlib only — sqlite3, json, sys, os, re)
"""

import sqlite3
import json
import sys
import os
import re


def find_sql_and_err(run_dir):
    """Locate eplusout.sql and eplusout.err, handling both run/ and parent dir."""
    sql_path = os.path.join(run_dir, 'eplusout.sql')
    err_path = os.path.join(run_dir, 'eplusout.err')

    if not os.path.exists(sql_path):
        # Try run/ subdirectory
        sql_path = os.path.join(run_dir, 'run', 'eplusout.sql')
        err_path = os.path.join(run_dir, 'run', 'eplusout.err')

    if not os.path.exists(sql_path):
        print(f"ERROR: Cannot find eplusout.sql in {run_dir} or {run_dir}/run/",
              file=sys.stderr)
        sys.exit(1)

    return sql_path, err_path


def query_facility_unmet(conn):
    """Extract facility-level occupied heating/cooling unmet hours."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'AnnualBuildingUtilityPerformanceSummary'
        AND TableName LIKE '%Comfort and Setpoint%'
    """)
    result = {}
    for row_name, value in c.fetchall():
        try:
            result[row_name.strip()] = float(value)
        except (ValueError, TypeError):
            result[row_name.strip()] = value.strip() if value else None
    return result


def query_zone_unmet(conn):
    """Extract zone-by-zone occupied heating and cooling unmet hours."""
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
        try:
            zones[name][col_name.strip()] = float(value)
        except (ValueError, TypeError):
            zones[name][col_name.strip()] = 0.0
    return zones


def query_end_uses(conn):
    """Extract end-use energy breakdown from AnnualBuildingUtilityPerformanceSummary."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'AnnualBuildingUtilityPerformanceSummary'
        AND TableName = 'End Uses'
    """)
    end_uses = {}
    for row_name, col_name, value in c.fetchall():
        row = row_name.strip()
        if not row:
            row = 'Total'  # E+ uses empty RowName for the total row
        col = col_name.strip()
        if row not in end_uses:
            end_uses[row] = {}
        try:
            end_uses[row][col] = float(value)
        except (ValueError, TypeError):
            end_uses[row][col] = 0.0
    return end_uses


def query_site_energy(conn):
    """Extract site/source energy and EUI."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'AnnualBuildingUtilityPerformanceSummary'
        AND TableName = 'Site and Source Energy'
    """)
    result = {}
    for row_name, col_name, value in c.fetchall():
        key = f"{row_name.strip()} [{col_name.strip()}]"
        try:
            result[key] = float(value)
        except (ValueError, TypeError):
            result[key] = value.strip() if value else None
    return result


def query_building_area(conn):
    """Extract building area."""
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'AnnualBuildingUtilityPerformanceSummary'
        AND TableName = 'Building Area'
    """)
    result = {}
    for row_name, col_name, value in c.fetchall():
        key = f"{row_name.strip()} [{col_name.strip()}]"
        try:
            result[key] = float(value)
        except (ValueError, TypeError):
            result[key] = value.strip() if value else None
    return result


def count_severe_errors(err_path):
    """Parse eplusout.err for severe error count from the summary line."""
    if not os.path.exists(err_path):
        return None

    severe_count = 0
    with open(err_path, 'r', errors='replace') as f:
        for line in f:
            # Match the EnergyPlus summary line: "X Severe Errors"
            m = re.search(r'(\d+)\s+Severe\s+Errors', line)
            if m:
                severe_count = int(m.group(1))
    return severe_count


def extract_results(run_dir):
    """Main extraction — returns structured dict."""
    sql_path, err_path = find_sql_and_err(run_dir)
    conn = sqlite3.connect(sql_path)

    results = {
        'run_dir': os.path.abspath(run_dir),
        'sql_path': os.path.abspath(sql_path),
        'facility_unmet': query_facility_unmet(conn),
        'zone_unmet': query_zone_unmet(conn),
        'end_uses': query_end_uses(conn),
        'site_energy': query_site_energy(conn),
        'building_area': query_building_area(conn),
        'severe_errors': count_severe_errors(err_path),
    }

    # Derive convenience fields
    fu = results['facility_unmet']
    results['summary'] = {
        'heating_unmet_occupied': fu.get(
            'Time Setpoint Not Met During Occupied Heating', None),
        'cooling_unmet_occupied': fu.get(
            'Time Setpoint Not Met During Occupied Cooling', None),
        'severe_errors': results['severe_errors'],
    }

    # Fan energy (sum across all fuel types)
    fan_row = results['end_uses'].get('Fans', {})
    results['summary']['fan_energy_gj'] = sum(fan_row.values())

    # Total site energy
    se = results['site_energy']
    results['summary']['total_site_energy_gj'] = se.get(
        'Total Site Energy [Total Energy]', None)
    # EnergyPlus stores EUI in MJ/m2; convert to kBtu/ft2
    eui_mj_m2 = se.get(
        'Net Site Energy [Energy Per Conditioned Building Area]', None)
    if eui_mj_m2 is not None:
        results['summary']['site_eui_mj_m2'] = eui_mj_m2
        results['summary']['site_eui_kbtu_ft2'] = eui_mj_m2 * 0.088055
    else:
        results['summary']['site_eui_kbtu_ft2'] = None
    results['summary']['total_source_energy_gj'] = se.get(
        'Total Source Energy [Total Energy]', None)

    conn.close()
    return results


def format_markdown(results):
    """Format results as markdown tables."""
    lines = []
    s = results['summary']

    lines.append("## Simulation Results Summary\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Heating unmet hours (occupied) | **{s['heating_unmet_occupied']:.1f}** |")
    lines.append(f"| Cooling unmet hours (occupied) | {s['cooling_unmet_occupied']:.1f} |")
    lines.append(f"| Severe errors | {s['severe_errors']} |")
    lines.append(f"| Fan energy | {s['fan_energy_gj']:.2f} GJ |")
    lines.append(f"| Total site energy | {s['total_site_energy_gj']:.2f} GJ |")
    if s.get('site_eui_kbtu_ft2'):
        lines.append(f"| Site EUI | {s['site_eui_kbtu_ft2']:.2f} kBtu/ft² |")
    lines.append("")

    # Zone-by-zone unmet hours
    zones = results['zone_unmet']
    sorted_zones = sorted(zones.items(),
                          key=lambda x: x[1].get('During Occupied Heating', 0),
                          reverse=True)

    lines.append("## Zone-by-Zone Occupied Heating Unmet Hours\n")
    lines.append("| Zone | Occ Heating (hr) | Occ Cooling (hr) | Total Heating (hr) |")
    lines.append("|------|-------------------|-------------------|---------------------|")
    sum_occ_htg = 0
    for zone_name, data in sorted_zones:
        oh = data.get('During Occupied Heating', 0)
        oc = data.get('During Occupied Cooling', 0)
        th = data.get('During Heating', 0)
        sum_occ_htg += oh
        if oh > 0 or oc > 0:
            lines.append(f"| {zone_name} | {oh:.1f} | {oc:.1f} | {th:.1f} |")
    lines.append(f"| **ZONE SUM** | **{sum_occ_htg:.1f}** | | |")
    lines.append("")

    # End-use energy
    lines.append("## End-Use Energy (GJ)\n")
    eu = results['end_uses']
    # Determine which fuel columns have data
    all_cols = set()
    for row_data in eu.values():
        for col, val in row_data.items():
            if val and val != 0:
                all_cols.add(col)
    # Standard column order
    col_order = ['Electricity', 'Natural Gas', 'Gasoline', 'Diesel', 'Coal',
                 'Fuel Oil No 1', 'Fuel Oil No 2', 'Propane', 'Other Fuel 1',
                 'Other Fuel 2', 'District Cooling', 'District Heating Water',
                 'District Heating Steam']
    active_cols = [c for c in col_order if c in all_cols]

    header = "| End Use | " + " | ".join(active_cols) + " | Total |"
    sep = "|---------|" + "|".join(["------" for _ in active_cols]) + "|-------|"
    lines.append(header)
    lines.append(sep)

    row_order = ['Heating', 'Cooling', 'Interior Lighting', 'Interior Equipment',
                 'Exterior Lighting', 'Exterior Equipment', 'Fans', 'Pumps',
                 'Heat Rejection', 'Humidification', 'Heat Recovery',
                 'Water Systems', 'Refrigeration', 'Generators']
    for row_name in row_order:
        if row_name in eu:
            row_data = eu[row_name]
            total = sum(row_data.get(c, 0) for c in active_cols)
            if total == 0:
                continue
            vals = " | ".join(f"{row_data.get(c, 0):.2f}" for c in active_cols)
            lines.append(f"| {row_name} | {vals} | {total:.2f} |")

    # Total row
    if 'Total' in eu:
        row_data = eu['Total']
        total = sum(row_data.get(c, 0) for c in active_cols)
        vals = " | ".join(f"{row_data.get(c, 0):.2f}" for c in active_cols)
        lines.append(f"| **Total** | {vals} | **{total:.2f}** |")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    run_dir = sys.argv[1]
    fmt = 'both'
    output_file = None

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == '--format' and i + 1 < len(sys.argv):
            fmt = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--output' and i + 1 < len(sys.argv):
            output_file = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    results = extract_results(run_dir)

    if fmt in ('json', 'both'):
        json_str = json.dumps(results, indent=2, default=str)
        if output_file:
            with open(output_file, 'w') as f:
                f.write(json_str)
            print(f"JSON written to {output_file}")
        if fmt == 'json':
            print(json_str)

    if fmt in ('markdown', 'both'):
        md = format_markdown(results)
        print(md)

    if fmt == 'both' and not output_file:
        print("\n---\n## Raw JSON\n```json")
        print(json.dumps(results['summary'], indent=2, default=str))
        print("```")


if __name__ == '__main__':
    main()
