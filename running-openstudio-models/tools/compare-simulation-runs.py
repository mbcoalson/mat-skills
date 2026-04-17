"""
compare-simulation-runs.py — Side-by-side comparison of two EnergyPlus runs

Queries two eplusout.sql databases and produces comparison tables with deltas
and percentage changes for all standard metrics.

Usage:
    python compare-simulation-runs.py <baseline-run-dir> <modified-run-dir>
    python compare-simulation-runs.py <baseline-run-dir> <modified-run-dir> --labels "v9" "v10"
    python compare-simulation-runs.py <baseline-run-dir> <modified-run-dir> --output comparison.md
    python compare-simulation-runs.py <baseline-run-dir> <modified-run-dir> --json comparison.json

Arguments:
    baseline-run-dir    Path to baseline run directory (containing eplusout.sql)
    modified-run-dir    Path to modified run directory (containing eplusout.sql)

Options:
    --labels A B        Labels for the two runs (default: "Baseline" "Modified")
    --output FILE       Write markdown to file instead of stdout
    --json FILE         Write structured JSON comparison to file
    --threshold N       Only show zones with >N unmet hours in either run (default: 0)

Output:
    - Facility-level unmet hours comparison
    - Zone-by-zone occupied heating unmet hours with deltas
    - End-use energy comparison with deltas (GJ)
    - Site energy / EUI comparison
    - Severe error comparison

Python: 3.x (stdlib only — sqlite3, json, sys, os)
"""

import sqlite3
import json
import sys
import os


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


def query_facility_unmet(db_path):
    conn = sqlite3.connect(db_path)
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
            pass
    conn.close()
    return result


def query_zone_unmet(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'SystemSummary'
        AND TableName = 'Time Setpoint Not Met'
        AND ColumnName IN ('During Occupied Heating', 'During Occupied Cooling')
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
    conn.close()
    return zones


def query_end_uses(db_path):
    conn = sqlite3.connect(db_path)
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
            row = 'Total'
        col = col_name.strip()
        if row not in end_uses:
            end_uses[row] = {}
        try:
            end_uses[row][col] = float(value)
        except (ValueError, TypeError):
            end_uses[row][col] = 0.0
    conn.close()
    return end_uses


def query_site_energy(db_path):
    conn = sqlite3.connect(db_path)
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
            pass
    conn.close()
    return result


def count_severe(run_dir):
    import re
    err_path = find_err(run_dir)
    if not err_path:
        return None
    with open(err_path, 'r', errors='replace') as f:
        for line in f:
            m = re.search(r'(\d+)\s+Severe\s+Errors', line)
            if m:
                return int(m.group(1))
    return 0


def delta_str(base, mod):
    """Format a delta with sign and percentage."""
    d = mod - base
    if base != 0:
        pct = d / base * 100
        return f"{d:+.1f} ({pct:+.1f}%)"
    elif d != 0:
        return f"{d:+.1f} (new)"
    return "0.0"


def compare(baseline_dir, modified_dir, label_a="Baseline", label_b="Modified",
            threshold=0):
    """Run full comparison, return markdown string and structured data."""
    sql_a = find_sql(baseline_dir)
    sql_b = find_sql(modified_dir)

    lines = []

    # === Facility Unmet ===
    fu_a = query_facility_unmet(sql_a)
    fu_b = query_facility_unmet(sql_b)

    htg_a = fu_a.get('Time Setpoint Not Met During Occupied Heating', 0)
    htg_b = fu_b.get('Time Setpoint Not Met During Occupied Heating', 0)
    clg_a = fu_a.get('Time Setpoint Not Met During Occupied Cooling', 0)
    clg_b = fu_b.get('Time Setpoint Not Met During Occupied Cooling', 0)
    sev_a = count_severe(baseline_dir)
    sev_b = count_severe(modified_dir)

    lines.append(f"## Facility-Level Comparison ({label_a} vs {label_b})\n")
    lines.append(f"| Metric | {label_a} | {label_b} | Delta |")
    lines.append("|--------|---------|---------|-------|")
    lines.append(f"| Heating unmet (occupied) | {htg_a:.1f} hr | {htg_b:.1f} hr | {delta_str(htg_a, htg_b)} |")
    lines.append(f"| Cooling unmet (occupied) | {clg_a:.1f} hr | {clg_b:.1f} hr | {delta_str(clg_a, clg_b)} |")
    lines.append(f"| Severe errors | {sev_a} | {sev_b} | {(sev_b or 0) - (sev_a or 0)} |")

    # Fan energy
    eu_a = query_end_uses(sql_a)
    eu_b = query_end_uses(sql_b)
    fan_a = sum(eu_a.get('Fans', {}).values())
    fan_b = sum(eu_b.get('Fans', {}).values())
    lines.append(f"| Fan energy | {fan_a:.2f} GJ | {fan_b:.2f} GJ | {delta_str(fan_a, fan_b)} |")

    # Site energy
    se_a = query_site_energy(sql_a)
    se_b = query_site_energy(sql_b)
    tse_a = se_a.get('Total Site Energy [Total Energy]', 0)
    tse_b = se_b.get('Total Site Energy [Total Energy]', 0)
    # EnergyPlus stores EUI in MJ/m2; convert to kBtu/ft2
    eui_a_mj = se_a.get('Net Site Energy [Energy Per Conditioned Building Area]', 0)
    eui_b_mj = se_b.get('Net Site Energy [Energy Per Conditioned Building Area]', 0)
    eui_a = eui_a_mj * 0.088055  # MJ/m2 -> kBtu/ft2
    eui_b = eui_b_mj * 0.088055
    lines.append(f"| Total site energy | {tse_a:.2f} GJ | {tse_b:.2f} GJ | {delta_str(tse_a, tse_b)} |")
    lines.append(f"| Site EUI | {eui_a:.2f} kBtu/ft2 | {eui_b:.2f} kBtu/ft2 | {delta_str(eui_a, eui_b)} |")
    lines.append("")

    # === Zone-by-Zone ===
    zu_a = query_zone_unmet(sql_a)
    zu_b = query_zone_unmet(sql_b)
    all_zones = sorted(set(list(zu_a.keys()) + list(zu_b.keys())))

    lines.append(f"## Zone-by-Zone Occupied Heating Unmet Hours\n")
    lines.append(f"| Zone | {label_a} (hr) | {label_b} (hr) | Delta | % Change |")
    lines.append("|------|---------|---------|-------|----------|")

    sum_a = sum_b = 0
    zone_data = []
    for z in all_zones:
        ha = zu_a.get(z, {}).get('During Occupied Heating', 0)
        hb = zu_b.get(z, {}).get('During Occupied Heating', 0)
        zone_data.append((z, ha, hb))

    # Sort by baseline hours descending
    zone_data.sort(key=lambda x: x[1], reverse=True)

    for z, ha, hb in zone_data:
        if ha < threshold and hb < threshold:
            continue
        d = hb - ha
        pct = (d / ha * 100) if ha > 0 else (100 if hb > 0 else 0)
        sum_a += ha
        sum_b += hb
        lines.append(f"| {z} | {ha:.1f} | {hb:.1f} | {d:+.1f} | {pct:+.1f}% |")

    td = sum_b - sum_a
    tp = (td / sum_a * 100) if sum_a > 0 else 0
    lines.append(f"| **ZONE SUM** | **{sum_a:.1f}** | **{sum_b:.1f}** | **{td:+.1f}** | **{tp:+.1f}%** |")
    lines.append("")

    # === End-Use Comparison ===
    lines.append(f"## End-Use Energy Comparison (GJ)\n")

    row_order = ['Heating', 'Cooling', 'Interior Lighting', 'Interior Equipment',
                 'Exterior Lighting', 'Exterior Equipment', 'Fans', 'Pumps',
                 'Heat Rejection', 'Humidification', 'Heat Recovery',
                 'Water Systems', 'Refrigeration', 'Generators']

    lines.append(f"| End Use | {label_a} (GJ) | {label_b} (GJ) | Delta |")
    lines.append("|---------|---------|---------|-------|")

    for row_name in row_order:
        ra = sum(eu_a.get(row_name, {}).values())
        rb = sum(eu_b.get(row_name, {}).values())
        if ra == 0 and rb == 0:
            continue
        lines.append(f"| {row_name} | {ra:.2f} | {rb:.2f} | {delta_str(ra, rb)} |")

    # Total
    ta = sum(eu_a.get('Total', {}).values())
    tb = sum(eu_b.get('Total', {}).values())
    if ta > 0 or tb > 0:
        lines.append(f"| **Total** | **{ta:.2f}** | **{tb:.2f}** | **{delta_str(ta, tb)}** |")
    lines.append("")

    # === Structured data for JSON ===
    comparison_data = {
        'baseline': {'dir': os.path.abspath(baseline_dir), 'label': label_a},
        'modified': {'dir': os.path.abspath(modified_dir), 'label': label_b},
        'facility': {
            'heating_unmet': {'baseline': htg_a, 'modified': htg_b,
                              'delta': htg_b - htg_a},
            'cooling_unmet': {'baseline': clg_a, 'modified': clg_b,
                              'delta': clg_b - clg_a},
            'severe_errors': {'baseline': sev_a, 'modified': sev_b},
            'fan_energy_gj': {'baseline': fan_a, 'modified': fan_b,
                              'delta': fan_b - fan_a},
            'site_energy_gj': {'baseline': tse_a, 'modified': tse_b,
                               'delta': tse_b - tse_a},
            'site_eui': {'baseline': eui_a, 'modified': eui_b,
                         'delta': eui_b - eui_a},
        },
        'zones': [
            {'zone': z, 'baseline': ha, 'modified': hb, 'delta': hb - ha}
            for z, ha, hb in zone_data
        ],
    }

    return "\n".join(lines), comparison_data


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    baseline_dir = sys.argv[1]
    modified_dir = sys.argv[2]
    label_a = "Baseline"
    label_b = "Modified"
    output_file = None
    json_file = None
    threshold = 0

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == '--labels' and i + 2 < len(sys.argv):
            label_a = sys.argv[i + 1]
            label_b = sys.argv[i + 2]
            i += 3
        elif sys.argv[i] == '--output' and i + 1 < len(sys.argv):
            output_file = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--json' and i + 1 < len(sys.argv):
            json_file = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--threshold' and i + 1 < len(sys.argv):
            threshold = float(sys.argv[i + 1])
            i += 2
        else:
            i += 1

    md, data = compare(baseline_dir, modified_dir, label_a, label_b, threshold)

    if output_file:
        with open(output_file, 'w') as f:
            f.write(md)
        print(f"Markdown written to {output_file}")
    else:
        print(md)

    if json_file:
        with open(json_file, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        print(f"JSON written to {json_file}")


if __name__ == '__main__':
    main()
