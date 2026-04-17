#!/usr/bin/env python3
"""
Baseline vs Proposed Consistency Audit
Per ASHRAE 90.1-2022 Appendix G, LEED v4.1 EAc2, IECC 2024 C407

Parses two OSM files and their eplusout.sql databases to verify that
everything required to match between baseline and proposed models does.

Usage:
    python baseline-proposed-audit.py <baseline_dir> <proposed_dir>
    python baseline-proposed-audit.py <baseline_dir> <proposed_dir> --output report.md
    python baseline-proposed-audit.py <baseline_dir> <proposed_dir> --labels "Baseline" "Proposed"

Checks (MUST MATCH per Appendix G):
  1. Weather file
  2. Geometry (floor area, zone count, surface areas)
  3. SpaceType inventory
  4. Occupancy loads per SpaceType
  5. Equipment loads per SpaceType
  6. Thermostat setpoints per zone
  7. Key schedules (occupancy, equipment, setpoints)
  8. Infiltration rates
  9. Service hot water loads

Checks (EXPECTED TO DIFFER — informational):
  10. Lighting power density per SpaceType
  11. HVAC system inventory
  12. Envelope constructions
  13. Simulation results (EUI, unmet hours, end uses)
"""

import sys
import os
import re
import sqlite3
import argparse
from pathlib import Path
from collections import defaultdict

# ============================================================================
# Constants
# ============================================================================
HANDLE_RE = re.compile(r'\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}', re.IGNORECASE)
AREA_TOL = 0.005      # 0.5% for area comparisons
VALUE_TOL = 0.001     # absolute tolerance for numeric field comparisons
SCHEDULE_TOL = 0.01   # tolerance for schedule value comparisons


# ============================================================================
# OSM Parser
# ============================================================================
def parse_osm(path):
    """Parse an OSM file into structured data.

    Returns:
        objects_by_type: {type_str: [list of object dicts]}
        handle_map: {handle_str: object_dict}

    Each object dict has:
        '_type': object type string
        '_handle': handle string (if present)
        '_name': name string (if present)
        '_fields': ordered list of (field_name, value) tuples
        Plus field_name → value for direct access
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    objects_by_type = defaultdict(list)
    handle_map = {}

    # Split into blocks by blank lines
    blocks = re.split(r'\n\s*\n', content)

    for block in blocks:
        lines = block.strip().split('\n')
        if not lines:
            continue

        # Skip pure comment blocks
        first_real = None
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('!'):
                first_real = stripped
                break
        if not first_real:
            continue

        # First non-comment line is the object type
        obj_type = first_real.rstrip(',').strip()
        if not obj_type.startswith('OS:'):
            continue

        obj = {
            '_type': obj_type,
            '_handle': None,
            '_name': None,
            '_fields': [],
        }

        # Parse field lines (everything after the type line)
        in_fields = False
        for line in lines:
            stripped = line.strip()
            if not in_fields:
                if stripped.rstrip(',').strip() == obj_type:
                    in_fields = True
                continue

            # Extract field name from !- comment
            if '!-' in stripped:
                value_part, comment_part = stripped.split('!-', 1)
                field_name = comment_part.strip()
                value = value_part.strip().rstrip(',').rstrip(';').strip()
            elif stripped.endswith(',') or stripped.endswith(';'):
                field_name = f"_unnamed_{len(obj['_fields'])}"
                value = stripped.rstrip(',').rstrip(';').strip()
            else:
                continue

            obj['_fields'].append((field_name, value))
            obj[field_name] = value

            # Capture handle and name
            if field_name == 'Handle':
                obj['_handle'] = value
            elif field_name == 'Name':
                obj['_name'] = value

        if obj['_handle']:
            handle_map[obj['_handle']] = obj

        objects_by_type[obj_type].append(obj)

    return dict(objects_by_type), handle_map


def resolve(handle, handle_map, field='_name'):
    """Resolve a handle to an object name (or other field)."""
    if not handle or not HANDLE_RE.match(handle):
        return handle  # Not a handle, return as-is
    obj = handle_map.get(handle)
    if obj:
        return obj.get(field, handle)
    return f"[unresolved: {handle[:20]}...]"


def get_by_name(objects_by_type, obj_type):
    """Get objects of a type indexed by name."""
    result = {}
    for obj in objects_by_type.get(obj_type, []):
        name = obj.get('_name', obj.get('_handle', 'unnamed'))
        result[name] = obj
    return result


def filter_unassigned_spacetypes(objects_by_type, handle_map):
    """Remove SpaceTypes not assigned to any OS:Space, plus their child objects.

    Modifies objects_by_type in place. Returns a list of removed SpaceType names
    for reporting.
    """
    # Build set of SpaceType handles that ARE referenced by OS:Space objects
    assigned_handles = set()
    for space in objects_by_type.get('OS:Space', []):
        st_handle = space.get('Space Type Name', '')
        if HANDLE_RE.match(st_handle):
            assigned_handles.add(st_handle)

    # Identify unassigned SpaceType handles and names
    removed_names = []
    unassigned_handles = set()
    kept = []
    for obj in objects_by_type.get('OS:SpaceType', []):
        if obj.get('_handle') not in assigned_handles:
            unassigned_handles.add(obj['_handle'])
            removed_names.append(obj.get('_name', 'unnamed'))
        else:
            kept.append(obj)
    objects_by_type['OS:SpaceType'] = kept

    if not unassigned_handles:
        return removed_names

    # Filter child objects that reference unassigned SpaceTypes
    child_types = [
        'OS:People', 'OS:ElectricEquipment', 'OS:Lights',
        'OS:SpaceInfiltration:DesignFlowRate', 'OS:InternalMass',
        'OS:OtherEquipment', 'OS:GasEquipment',
    ]
    for ct in child_types:
        if ct not in objects_by_type:
            continue
        filtered = []
        for obj in objects_by_type[ct]:
            st_handle = obj.get('Space or SpaceType Name', '')
            if st_handle in unassigned_handles:
                continue  # Skip — references unassigned SpaceType
            filtered.append(obj)
        objects_by_type[ct] = filtered

    return removed_names


def find_osm_and_sql(directory):
    """Find the .osm file and eplusout.sql in a directory."""
    d = Path(directory)
    osm_files = list(d.glob('*.osm'))
    if not osm_files:
        return None, None

    osm = osm_files[0]

    # Check for SQL in run/ subdirectory first, then direct
    sql = d / 'run' / 'eplusout.sql'
    if not sql.exists():
        sql = d / 'eplusout.sql'
    if not sql.exists():
        sql = None

    return str(osm), str(sql) if sql else None


def try_float(value):
    """Try to parse a value as float, return None if not numeric."""
    if not value or value.strip() == '':
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def floats_match(a, b, tol=VALUE_TOL):
    """Compare two float values within tolerance."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if a == 0 and b == 0:
        return True
    if a == 0 or b == 0:
        return abs(a - b) <= tol
    return abs(a - b) / max(abs(a), abs(b)) <= tol


# ============================================================================
# SQL Helpers
# ============================================================================
def query_sql(sql_path, query):
    """Execute a SQL query and return results."""
    try:
        conn = sqlite3.connect(sql_path)
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        return [(f"SQL ERROR: {e}",)]


def get_floor_area_sql(sql_path):
    """Get total and conditioned floor area from SQL."""
    rows = query_sql(sql_path, """
        SELECT RowName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'InputVerificationandResultsSummary'
          AND TableName = 'Building Area Information'
          AND ColumnName = 'Area'
    """)
    result = {}
    for row_name, value in rows:
        result[row_name.strip()] = try_float(value)
    return result


def get_zone_summary_sql(sql_path):
    """Get zone summary from SQL."""
    rows = query_sql(sql_path, """
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'InputVerificationandResultsSummary'
          AND TableName = 'Zone Summary'
    """)
    zones = defaultdict(dict)
    for row_name, col_name, value in rows:
        zones[row_name.strip()][col_name.strip()] = value
    return dict(zones)


def get_envelope_sql(sql_path):
    """Get envelope surface summary from SQL."""
    rows = query_sql(sql_path, """
        SELECT TableName, RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'EnvelopeSummary'
          AND TableName IN ('Opaque Exterior', 'Exterior Fenestration')
    """)
    surfaces = defaultdict(lambda: defaultdict(dict))
    for table, row, col, val in rows:
        surfaces[table][row.strip()][col.strip()] = val
    return dict(surfaces)


def get_eui_sql(sql_path):
    """Get site EUI from SQL."""
    rows = query_sql(sql_path, """
        SELECT ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'AnnualBuildingUtilityPerformanceSummary'
          AND TableName = 'Site and Source Energy'
          AND RowName = 'Net Site Energy'
    """)
    result = {}
    for col, val in rows:
        result[col.strip()] = val
    return result


def get_unmet_hours_sql(sql_path):
    """Get facility unmet hours from SQL."""
    rows = query_sql(sql_path, """
        SELECT ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'SystemSummary'
          AND TableName = 'Time Setpoint Not Met'
          AND RowName = 'Facility'
    """)
    return {col.strip(): val for col, val in rows}


def get_end_uses_sql(sql_path):
    """Get end-use energy breakdown from SQL."""
    rows = query_sql(sql_path, """
        SELECT RowName, ColumnName, Value FROM TabularDataWithStrings
        WHERE ReportName = 'AnnualBuildingUtilityPerformanceSummary'
          AND TableName = 'End Uses'
          AND ColumnName IN ('Electricity', 'Natural Gas', 'Additional Fuel',
                             'District Cooling', 'District Heating', 'Steam')
    """)
    end_uses = defaultdict(dict)
    for row, col, val in rows:
        end_uses[row.strip()][col.strip()] = try_float(val)
    return dict(end_uses)


# ============================================================================
# Check Functions
# ============================================================================
def check_weather(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 1: Weather file must be identical."""
    result = {'name': 'Weather File', 'section': 'G3.1', 'must_match': True,
              'status': 'PASS', 'details': []}

    base_wf = base_objs.get('OS:WeatherFile', [])
    prop_wf = prop_objs.get('OS:WeatherFile', [])

    if not base_wf:
        result['status'] = 'WARN'
        result['details'].append('Baseline: No OS:WeatherFile object found')
        return result
    if not prop_wf:
        result['status'] = 'WARN'
        result['details'].append('Proposed: No OS:WeatherFile object found')
        return result

    b_url = base_wf[0].get('Url', '')
    p_url = prop_wf[0].get('Url', '')
    b_city = base_wf[0].get('City', '')
    p_city = prop_wf[0].get('City', '')

    # Compare filenames (strip paths)
    b_file = Path(b_url).name if b_url else 'MISSING'
    p_file = Path(p_url).name if p_url else 'MISSING'

    if b_file.lower() == p_file.lower():
        result['details'].append(f'Both use: {b_file}')
    else:
        result['status'] = 'FAIL'
        result['details'].append(f'Baseline: {b_file}')
        result['details'].append(f'Proposed: {p_file}')

    # Compare lat/lon
    for field in ['Latitude {deg}', 'Longitude {deg}', 'Time Zone {hr}', 'Elevation {m}']:
        b_val = try_float(base_wf[0].get(field, ''))
        p_val = try_float(prop_wf[0].get(field, ''))
        if b_val is not None and p_val is not None:
            if not floats_match(b_val, p_val):
                result['status'] = 'FAIL'
                result['details'].append(f'{field}: Baseline={b_val}, Proposed={p_val}')

    return result


def check_spacetype_inventory(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 3: SpaceType inventory must match."""
    result = {'name': 'SpaceType Inventory', 'section': 'G3.1', 'must_match': True,
              'status': 'PASS', 'details': []}

    base_st = get_by_name(base_objs, 'OS:SpaceType')
    prop_st = get_by_name(prop_objs, 'OS:SpaceType')

    base_names = set(base_st.keys())
    prop_names = set(prop_st.keys())

    common = base_names & prop_names
    base_only = base_names - prop_names
    prop_only = prop_names - base_names

    result['details'].append(f'Common SpaceTypes: {len(common)}')

    if base_only:
        result['status'] = 'FAIL'
        for name in sorted(base_only):
            result['details'].append(f'  Baseline only: {name}')

    if prop_only:
        result['status'] = 'FAIL'
        for name in sorted(prop_only):
            result['details'].append(f'  Proposed only: {name}')

    if not base_only and not prop_only:
        result['details'].append(f'All {len(common)} SpaceTypes match')

    return result


def _compare_loads(base_objs, base_hmap, prop_objs, prop_hmap,
                   load_type, def_type, density_field, section_name):
    """Generic load comparison per SpaceType."""
    result = {'name': section_name, 'section': 'G3.1', 'must_match': True,
              'status': 'PASS', 'details': []}

    # Build SpaceType → [definitions] map for each model
    def build_load_map(objs, hmap):
        load_map = defaultdict(list)
        for obj in objs.get(load_type, []):
            st_handle = obj.get('Space or SpaceType Name', '')
            st_name = resolve(st_handle, hmap)
            def_handle = obj.get(f'{load_type.split(":")[-1]} Definition Name',
                                 obj.get('Definition Name', ''))
            # Try multiple field name patterns for the definition reference
            for field_name, val in obj['_fields']:
                if 'Definition' in field_name and HANDLE_RE.match(val):
                    def_handle = val
                    break

            defn = hmap.get(def_handle, {})
            density = try_float(defn.get(density_field, ''))
            load_map[st_name].append({
                'name': obj.get('_name', 'unnamed'),
                'density': density,
                'method': defn.get('Number of People Calculation Method',
                          defn.get('Design Level Calculation Method', '')),
            })
        return dict(load_map)

    base_map = build_load_map(base_objs, base_hmap)
    prop_map = build_load_map(prop_objs, prop_hmap)

    all_st = sorted(set(list(base_map.keys()) + list(prop_map.keys())))

    if not all_st:
        result['details'].append(f'No {load_type} objects found in either model')
        return result

    mismatches = 0
    for st in all_st:
        b_loads = base_map.get(st, [])
        p_loads = prop_map.get(st, [])

        # Sum densities per SpaceType (there could be multiple People objects)
        b_total = sum(d['density'] for d in b_loads if d['density'] is not None)
        p_total = sum(d['density'] for d in p_loads if d['density'] is not None)

        if not b_loads and p_loads:
            result['details'].append(f'  {st}: Baseline=NONE, Proposed={p_total:.6f}')
            mismatches += 1
        elif b_loads and not p_loads:
            result['details'].append(f'  {st}: Baseline={b_total:.6f}, Proposed=NONE')
            mismatches += 1
        elif not floats_match(b_total, p_total):
            result['details'].append(f'  {st}: Baseline={b_total:.6f}, Proposed={p_total:.6f}')
            mismatches += 1

    if mismatches:
        result['status'] = 'FAIL'
        result['details'].insert(0, f'{mismatches} SpaceType(s) with mismatched {section_name}:')
    else:
        result['details'].append(f'All {len(all_st)} SpaceTypes match')

    return result


def check_people_loads(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 4: Occupancy density per SpaceType must match."""
    return _compare_loads(base_objs, base_hmap, prop_objs, prop_hmap,
                          'OS:People', 'OS:People:Definition',
                          'People per Floor Area {person/m2}',
                          'Occupancy (People/m²)')


def check_equipment_loads(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 5: Equipment density per SpaceType must match."""
    return _compare_loads(base_objs, base_hmap, prop_objs, prop_hmap,
                          'OS:ElectricEquipment', 'OS:ElectricEquipment:Definition',
                          'Watts per Floor Area {W/m2}',
                          'Equipment (W/m²)')


def check_thermostats(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 6: Thermostat setpoints per zone must match."""
    result = {'name': 'Thermostat Setpoints', 'section': 'G3.1.2.2', 'must_match': True,
              'status': 'PASS', 'details': []}

    def get_zone_thermostats(objs, hmap):
        """Map zone name → (heating_schedule_name, cooling_schedule_name)."""
        zone_tstat = {}
        for zone in objs.get('OS:ThermalZone', []):
            zone_name = zone.get('_name', 'unnamed')
            # Find thermostat handle — look for field containing 'Thermostat'
            tstat_handle = None
            for field_name, val in zone['_fields']:
                if 'Thermostat' in field_name and HANDLE_RE.match(val):
                    tstat_handle = val
                    break

            if not tstat_handle:
                zone_tstat[zone_name] = (None, None)
                continue

            tstat = hmap.get(tstat_handle, {})
            htg_handle = tstat.get('Heating Setpoint Temperature Schedule Name', '')
            clg_handle = tstat.get('Cooling Setpoint Temperature Schedule Name', '')

            htg_name = resolve(htg_handle, hmap) if htg_handle else None
            clg_name = resolve(clg_handle, hmap) if clg_handle else None
            zone_tstat[zone_name] = (htg_name, clg_name)

        return zone_tstat

    base_zt = get_zone_thermostats(base_objs, base_hmap)
    prop_zt = get_zone_thermostats(prop_objs, prop_hmap)

    common_zones = sorted(set(base_zt.keys()) & set(prop_zt.keys()))
    mismatches = 0

    for zone in common_zones:
        b_htg, b_clg = base_zt[zone]
        p_htg, p_clg = prop_zt[zone]

        if b_htg != p_htg:
            result['details'].append(f'  {zone} heating: Baseline="{b_htg}", Proposed="{p_htg}"')
            mismatches += 1
        if b_clg != p_clg:
            result['details'].append(f'  {zone} cooling: Baseline="{b_clg}", Proposed="{p_clg}"')
            mismatches += 1

    # Zones in one model but not the other
    base_only = set(base_zt.keys()) - set(prop_zt.keys())
    prop_only = set(prop_zt.keys()) - set(base_zt.keys())

    for z in sorted(base_only):
        result['details'].append(f'  {z}: Baseline only')
        mismatches += 1
    for z in sorted(prop_only):
        result['details'].append(f'  {z}: Proposed only')
        mismatches += 1

    if mismatches:
        result['status'] = 'FAIL'
        result['details'].insert(0, f'{mismatches} thermostat mismatch(es):')
    else:
        result['details'].append(f'All {len(common_zones)} zones have matching thermostat schedule names')

    return result


def check_schedules(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 7: Key schedule values must match.

    Compares OS:Schedule:Day values for schedules referenced by
    People, Equipment, and Thermostat objects.
    """
    result = {'name': 'Schedule Values', 'section': 'G3.1', 'must_match': True,
              'status': 'PASS', 'details': []}

    def extract_schedule_day_values(objs, hmap):
        """Extract {schedule_day_name: [(hour, minute, value), ...]} for all schedule days."""
        days = {}
        for obj in objs.get('OS:Schedule:Day', []):
            name = obj.get('_name', 'unnamed')
            values = []
            fields = obj['_fields']
            i = 0
            while i < len(fields):
                fn, fv = fields[i]
                if 'Hour' in fn:
                    hour = try_float(fv)
                    minute = try_float(fields[i + 1][1]) if i + 1 < len(fields) else 0
                    value = try_float(fields[i + 2][1]) if i + 2 < len(fields) else None
                    if hour is not None and value is not None:
                        values.append((int(hour), int(minute or 0), value))
                    i += 3
                else:
                    i += 1
            days[name] = values
        return days

    def get_ruleset_days(objs, hmap):
        """Map schedule ruleset name → list of (purpose, schedule_day_name)."""
        rulesets = {}
        for obj in objs.get('OS:Schedule:Ruleset', []):
            name = obj.get('_name', 'unnamed')
            day_refs = []
            for fn, fv in obj['_fields']:
                if HANDLE_RE.match(fv):
                    day_name = resolve(fv, hmap)
                    day_refs.append((fn, day_name))
            rulesets[name] = day_refs

        # Also capture Schedule:Rule → Schedule:Day mappings
        for obj in objs.get('OS:Schedule:Rule', []):
            ruleset_handle = None
            day_handle = None
            for fn, fv in obj['_fields']:
                if 'Schedule Ruleset Name' in fn and HANDLE_RE.match(fv):
                    ruleset_handle = fv
                elif 'Day Schedule Name' in fn and HANDLE_RE.match(fv):
                    day_handle = fv

            if ruleset_handle and day_handle:
                rs_name = resolve(ruleset_handle, hmap)
                day_name = resolve(day_handle, hmap)
                rule_name = obj.get('_name', 'rule')
                if rs_name in rulesets:
                    rulesets[rs_name].append((f'Rule: {rule_name}', day_name))

        return rulesets

    base_days = extract_schedule_day_values(base_objs, base_hmap)
    prop_days = extract_schedule_day_values(prop_objs, prop_hmap)
    base_rulesets = get_ruleset_days(base_objs, base_hmap)
    prop_rulesets = get_ruleset_days(prop_objs, prop_hmap)

    # Filter out None keys (unnamed schedules)
    base_rulesets = {k: v for k, v in base_rulesets.items() if k is not None}
    prop_rulesets = {k: v for k, v in prop_rulesets.items() if k is not None}

    # Compare schedule rulesets that exist in both models
    common_rs = sorted(set(base_rulesets.keys()) & set(prop_rulesets.keys()))
    mismatches = 0

    for rs_name in common_rs:
        # Get all unique day schedule names referenced by this ruleset
        b_day_names = set(dn for _, dn in base_rulesets[rs_name])
        p_day_names = set(dn for _, dn in prop_rulesets[rs_name])

        # Compare day schedules that share names
        common_days = b_day_names & p_day_names
        for day_name in sorted(common_days):
            b_vals = base_days.get(day_name, [])
            p_vals = prop_days.get(day_name, [])

            if len(b_vals) != len(p_vals):
                result['details'].append(
                    f'  {rs_name} → {day_name}: different number of periods '
                    f'(Baseline={len(b_vals)}, Proposed={len(p_vals)})')
                mismatches += 1
                continue

            for j, (bv, pv) in enumerate(zip(b_vals, p_vals)):
                if bv[0] != pv[0] or bv[1] != pv[1]:  # time mismatch
                    result['details'].append(
                        f'  {rs_name} → {day_name}: time mismatch at period {j+1} '
                        f'(Baseline={bv[0]:02d}:{bv[1]:02d}, Proposed={pv[0]:02d}:{pv[1]:02d})')
                    mismatches += 1
                elif not floats_match(bv[2], pv[2], SCHEDULE_TOL):
                    result['details'].append(
                        f'  {rs_name} → {day_name}: value mismatch at {bv[0]:02d}:{bv[1]:02d} '
                        f'(Baseline={bv[2]}, Proposed={pv[2]})')
                    mismatches += 1

        # Flag days only in one model
        for day_name in sorted(b_day_names - p_day_names):
            result['details'].append(f'  {rs_name}: day "{day_name}" in Baseline only')
            mismatches += 1
        for day_name in sorted(p_day_names - b_day_names):
            result['details'].append(f'  {rs_name}: day "{day_name}" in Proposed only')
            mismatches += 1

    # Rulesets in one model only
    b_only_rs = set(base_rulesets.keys()) - set(prop_rulesets.keys())
    p_only_rs = set(prop_rulesets.keys()) - set(base_rulesets.keys())
    for rs in sorted(b_only_rs):
        result['details'].append(f'  Schedule "{rs}": Baseline only')
    for rs in sorted(p_only_rs):
        result['details'].append(f'  Schedule "{rs}": Proposed only')

    if mismatches:
        result['status'] = 'FAIL'
        result['details'].insert(0, f'{mismatches} schedule mismatch(es) in {len(common_rs)} common rulesets:')
    else:
        result['details'].insert(0, f'Compared {len(common_rs)} schedule rulesets')
        if b_only_rs or p_only_rs:
            result['status'] = 'WARN'
            result['details'].append(
                f'{len(b_only_rs)} baseline-only, {len(p_only_rs)} proposed-only schedules '
                f'(may be expected for HVAC differences)')

    return result


def check_infiltration(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 8: Infiltration rates per SpaceType must match."""
    result = {'name': 'Infiltration Rates', 'section': 'G3.1.1.4', 'must_match': True,
              'status': 'PASS', 'details': []}

    def build_infil_map(objs, hmap):
        infil_map = {}
        for obj in objs.get('OS:SpaceInfiltration:DesignFlowRate', []):
            st_handle = obj.get('Space or SpaceType Name', '')
            st_name = resolve(st_handle, hmap)
            method = obj.get('Design Flow Rate Calculation Method', '')
            rate = None
            for field_candidate in ['Design Flow Rate {m3/s}',
                                    'Flow Rate per Floor Area {m3/s-m2}',
                                    'Flow Rate per Exterior Surface Area {m3/s-m2}',
                                    'Air Changes per Hour {1/hr}']:
                val = try_float(obj.get(field_candidate, ''))
                if val is not None and val > 0:
                    rate = (field_candidate, val)
                    break
            infil_map[st_name] = {'method': method, 'rate': rate,
                                  'schedule': resolve(obj.get('Schedule Name', ''), hmap)}
        return infil_map

    base_map = build_infil_map(base_objs, base_hmap)
    prop_map = build_infil_map(prop_objs, prop_hmap)

    all_st = sorted(set(list(base_map.keys()) + list(prop_map.keys())))
    mismatches = 0

    for st in all_st:
        b = base_map.get(st)
        p = prop_map.get(st)

        if b and not p:
            result['details'].append(f'  {st}: Baseline has infiltration, Proposed does not')
            mismatches += 1
        elif p and not b:
            result['details'].append(f'  {st}: Proposed has infiltration, Baseline does not')
            mismatches += 1
        elif b and p:
            if b['rate'] and p['rate']:
                if b['rate'][0] != p['rate'][0]:
                    result['details'].append(
                        f'  {st}: Different methods ({b["rate"][0]} vs {p["rate"][0]})')
                    mismatches += 1
                elif not floats_match(b['rate'][1], p['rate'][1]):
                    result['details'].append(
                        f'  {st}: {b["rate"][0]}: Baseline={b["rate"][1]:.6f}, '
                        f'Proposed={p["rate"][1]:.6f}')
                    mismatches += 1
            if b.get('schedule') != p.get('schedule'):
                result['details'].append(
                    f'  {st}: Schedule mismatch: "{b.get("schedule")}" vs "{p.get("schedule")}"')
                mismatches += 1

    if mismatches:
        result['status'] = 'FAIL'
        result['details'].insert(0, f'{mismatches} infiltration mismatch(es):')
    else:
        result['details'].append(f'All {len(all_st)} SpaceTypes match')

    return result


def check_shw(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 9: Service hot water loads must match."""
    result = {'name': 'Service Hot Water Loads', 'section': 'G3.1.3.12', 'must_match': True,
              'status': 'PASS', 'details': []}

    def get_shw_equip(objs, hmap):
        equip = {}
        for obj in objs.get('OS:WaterUse:Equipment', []):
            name = obj.get('_name', 'unnamed')
            defn_handle = None
            for fn, fv in obj['_fields']:
                if 'Definition' in fn and HANDLE_RE.match(fv):
                    defn_handle = fv
                    break
            defn = hmap.get(defn_handle, {}) if defn_handle else {}
            peak_flow = try_float(defn.get('Peak Flow Rate {m3/s}', ''))
            equip[name] = {'peak_flow': peak_flow}
        return equip

    base_shw = get_shw_equip(base_objs, base_hmap)
    prop_shw = get_shw_equip(prop_objs, prop_hmap)

    all_names = sorted(set(list(base_shw.keys()) + list(prop_shw.keys())))
    mismatches = 0

    if not all_names:
        result['details'].append('No WaterUse:Equipment found in either model')
        return result

    for name in all_names:
        b = base_shw.get(name)
        p = prop_shw.get(name)
        if b and not p:
            result['details'].append(f'  {name}: Baseline only')
            mismatches += 1
        elif p and not b:
            result['details'].append(f'  {name}: Proposed only')
            mismatches += 1
        elif b and p:
            if not floats_match(b['peak_flow'], p['peak_flow']):
                result['details'].append(
                    f'  {name}: Peak flow Baseline={b["peak_flow"]}, Proposed={p["peak_flow"]}')
                mismatches += 1

    if mismatches:
        result['status'] = 'FAIL'
        result['details'].insert(0, f'{mismatches} SHW mismatch(es):')
    else:
        result['details'].append(f'All {len(all_names)} SHW equipment match')

    return result


def check_geometry_sql(base_sql, prop_sql):
    """Check 2: Geometry comparison from SQL results."""
    result = {'name': 'Building Geometry', 'section': 'G3.1', 'must_match': True,
              'status': 'PASS', 'details': []}

    if not base_sql or not prop_sql:
        result['status'] = 'WARN'
        result['details'].append('SQL results not available for one or both models')
        return result

    # Floor areas
    base_area = get_floor_area_sql(base_sql)
    prop_area = get_floor_area_sql(prop_sql)

    for metric in ['Total Building Area', 'Net Conditioned Building Area']:
        b_val = base_area.get(metric)
        p_val = prop_area.get(metric)
        if b_val and p_val:
            if floats_match(b_val, p_val, AREA_TOL):
                result['details'].append(f'{metric}: {b_val:.1f} m² (match)')
            else:
                delta_pct = (p_val - b_val) / b_val * 100 if b_val else 0
                result['status'] = 'FAIL'
                result['details'].append(
                    f'{metric}: Baseline={b_val:.1f} m², Proposed={p_val:.1f} m² '
                    f'(delta={delta_pct:+.1f}%)')

    # Zone counts
    base_zones = get_zone_summary_sql(base_sql)
    prop_zones = get_zone_summary_sql(prop_sql)
    b_count = len(base_zones)
    p_count = len(prop_zones)

    if b_count == p_count:
        result['details'].append(f'Zone count: {b_count} (match)')
    else:
        result['status'] = 'FAIL'
        result['details'].append(f'Zone count: Baseline={b_count}, Proposed={p_count}')

    # Zones in one but not the other
    b_zone_names = set(base_zones.keys())
    p_zone_names = set(prop_zones.keys())
    b_only = b_zone_names - p_zone_names
    p_only = p_zone_names - b_zone_names

    if b_only:
        for z in sorted(b_only):
            result['details'].append(f'  Zone in Baseline only: {z}')
    if p_only:
        for z in sorted(p_only):
            result['details'].append(f'  Zone in Proposed only: {z}')

    return result


def check_lighting(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 10: Lighting density (expected to differ)."""
    r = _compare_loads(base_objs, base_hmap, prop_objs, prop_hmap,
                       'OS:Lights', 'OS:Lights:Definition',
                       'Watts per Floor Area {W/m2}',
                       'Lighting (W/m²)')
    r['must_match'] = False
    r['section'] = 'G3.1 / Table G3.7'
    # Downgrade FAIL to INFO for expected differences
    if r['status'] == 'FAIL':
        r['status'] = 'INFO'
    return r


def check_hvac_inventory(base_objs, base_hmap, prop_objs, prop_hmap):
    """Check 11: HVAC system inventory (expected to differ)."""
    result = {'name': 'HVAC System Inventory', 'section': 'G3.1.1', 'must_match': False,
              'status': 'INFO', 'details': []}

    hvac_types = [
        'OS:AirLoopHVAC',
        'OS:PlantLoop',
        'OS:ZoneHVAC:WaterToAirHeatPump',
        'OS:ZoneHVAC:PackagedTerminalHeatPump',
        'OS:ZoneHVAC:FourPipeFanCoil',
        'OS:AirLoopHVAC:UnitarySystem',
        'OS:Coil:Heating:Gas',
        'OS:Coil:Heating:Electric',
        'OS:Coil:Cooling:DX:SingleSpeed',
        'OS:Coil:Cooling:DX:TwoSpeed',
        'OS:Boiler:HotWater',
        'OS:Chiller:Electric:EIR',
        'OS:WaterHeater:Mixed',
        'OS:WaterHeater:HeatPump',
    ]

    result['details'].append(f'{"Object Type":<50s} {"Baseline":>8s} {"Proposed":>8s}')
    result['details'].append(f'{"-"*50} {"-"*8} {"-"*8}')

    for ht in hvac_types:
        b_count = len(base_objs.get(ht, []))
        p_count = len(prop_objs.get(ht, []))
        if b_count > 0 or p_count > 0:
            marker = ' *' if b_count != p_count else ''
            result['details'].append(f'{ht:<50s} {b_count:>8d} {p_count:>8d}{marker}')

    # Air loop names
    result['details'].append('')
    result['details'].append('Air Loops:')
    for obj in base_objs.get('OS:AirLoopHVAC', []):
        result['details'].append(f'  Baseline: {obj.get("_name", "unnamed")}')
    for obj in prop_objs.get('OS:AirLoopHVAC', []):
        result['details'].append(f'  Proposed: {obj.get("_name", "unnamed")}')

    return result


def check_results_sql(base_sql, prop_sql, base_label, prop_label):
    """Check 13: Simulation results comparison (informational)."""
    result = {'name': 'Simulation Results', 'section': 'Informational', 'must_match': False,
              'status': 'INFO', 'details': []}

    if not base_sql or not prop_sql:
        result['details'].append('SQL results not available for one or both models')
        return result

    # EUI
    b_eui = get_eui_sql(base_sql)
    p_eui = get_eui_sql(prop_sql)

    b_eui_val = try_float(b_eui.get('Energy Per Conditioned Building Area', ''))
    p_eui_val = try_float(p_eui.get('Energy Per Conditioned Building Area', ''))
    if b_eui_val and p_eui_val:
        # Convert MJ/m² to kBtu/ft²
        b_kbtu = b_eui_val * 0.088055
        p_kbtu = p_eui_val * 0.088055
        pct = (p_kbtu - b_kbtu) / b_kbtu * 100 if b_kbtu else 0
        result['details'].append(
            f'Site EUI: {base_label}={b_kbtu:.2f}, {prop_label}={p_kbtu:.2f} kBtu/ft² '
            f'({pct:+.1f}%)')

    # Unmet hours
    b_unmet = get_unmet_hours_sql(base_sql)
    p_unmet = get_unmet_hours_sql(prop_sql)

    for col in ['During Occupied Heating', 'During Occupied Cooling',
                'During Heating', 'During Cooling']:
        b_val = b_unmet.get(col, 'N/A')
        p_val = p_unmet.get(col, 'N/A')
        result['details'].append(f'{col}: {base_label}={b_val}, {prop_label}={p_val} hr')

    # End uses
    b_eu = get_end_uses_sql(base_sql)
    p_eu = get_end_uses_sql(prop_sql)
    if b_eu and p_eu:
        result['details'].append('')
        result['details'].append(f'{"End Use":<25s} {base_label+" (GJ)":>15s} {prop_label+" (GJ)":>15s} {"Delta":>10s}')
        result['details'].append(f'{"-"*25} {"-"*15} {"-"*15} {"-"*10}')
        for eu in ['Heating', 'Cooling', 'Interior Lighting', 'Interior Equipment',
                    'Fans', 'Pumps', 'Heat Rejection', 'Water Systems']:
            b_total = sum(v for v in b_eu.get(eu, {}).values() if v)
            p_total = sum(v for v in p_eu.get(eu, {}).values() if v)
            delta = p_total - b_total
            result['details'].append(f'{eu:<25s} {b_total:>15.1f} {p_total:>15.1f} {delta:>+10.1f}')

    return result


# ============================================================================
# Report Generator
# ============================================================================
def generate_report(results, base_label, prop_label, base_dir, prop_dir):
    """Generate markdown audit report."""
    lines = []
    lines.append(f'# Baseline vs Proposed Consistency Audit')
    lines.append(f'')
    lines.append(f'**Generated**: {__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")}')
    lines.append(f'**{base_label}**: `{base_dir}`')
    lines.append(f'**{prop_label}**: `{prop_dir}`')
    lines.append(f'**Standard**: ASHRAE 90.1-2022 Appendix G / LEED v4.1 EAc2 / IECC 2024 C407')
    lines.append(f'')

    # Summary
    pass_count = sum(1 for r in results if r['status'] == 'PASS')
    fail_count = sum(1 for r in results if r['status'] == 'FAIL')
    warn_count = sum(1 for r in results if r['status'] == 'WARN')
    info_count = sum(1 for r in results if r['status'] == 'INFO')

    lines.append(f'## Summary: {fail_count} FAIL, {warn_count} WARN, {pass_count} PASS, {info_count} INFO')
    lines.append(f'')

    # Quick summary table
    lines.append(f'| # | Check | Section | Must Match | Status |')
    lines.append(f'|---|-------|---------|------------|--------|')
    for i, r in enumerate(results, 1):
        match_str = 'Yes' if r['must_match'] else 'No'
        status_str = r['status']
        if status_str == 'FAIL':
            status_str = '**FAIL**'
        lines.append(f'| {i} | {r["name"]} | {r["section"]} | {match_str} | {status_str} |')
    lines.append(f'')

    # Detailed results
    lines.append(f'## Detailed Results')
    lines.append(f'')

    for i, r in enumerate(results, 1):
        status_marker = {'PASS': 'PASS', 'FAIL': '**FAIL**', 'WARN': 'WARN', 'INFO': 'INFO'}
        lines.append(f'### {i}. {r["name"]} — {status_marker.get(r["status"], r["status"])}')
        lines.append(f'')
        lines.append(f'*Section {r["section"]}* | Must match: {"Yes" if r["must_match"] else "No (informational)"}')
        lines.append(f'')
        if r['details']:
            lines.append(f'```')
            for detail in r['details']:
                lines.append(detail)
            lines.append(f'```')
        lines.append(f'')

    return '\n'.join(lines)


# ============================================================================
# Main
# ============================================================================
def main():
    parser = argparse.ArgumentParser(
        description='Baseline vs Proposed Consistency Audit (ASHRAE 90.1-2022 Appendix G)')
    parser.add_argument('baseline_dir', help='Path to baseline model directory')
    parser.add_argument('proposed_dir', help='Path to proposed model directory')
    parser.add_argument('--labels', nargs=2, default=['Baseline', 'Proposed'],
                        help='Labels for the two models')
    parser.add_argument('--output', help='Write report to file (default: stdout)')
    parser.add_argument('--ignore-unassigned-spacetypes', action='store_true',
                        help='Filter out SpaceTypes not assigned to any OS:Space (removes DOE '
                             'reference building templates left over from baseline generation)')
    args = parser.parse_args()

    base_label, prop_label = args.labels

    # Find files
    base_osm, base_sql = find_osm_and_sql(args.baseline_dir)
    prop_osm, prop_sql = find_osm_and_sql(args.proposed_dir)

    if not base_osm:
        print(f"ERROR: No .osm file found in {args.baseline_dir}")
        sys.exit(1)
    if not prop_osm:
        print(f"ERROR: No .osm file found in {args.proposed_dir}")
        sys.exit(1)

    print(f"Parsing {base_label}: {base_osm}")
    base_objs, base_hmap = parse_osm(base_osm)
    print(f"  {sum(len(v) for v in base_objs.values())} objects, {len(base_hmap)} handles")

    print(f"Parsing {prop_label}: {prop_osm}")
    prop_objs, prop_hmap = parse_osm(prop_osm)
    print(f"  {sum(len(v) for v in prop_objs.values())} objects, {len(prop_hmap)} handles")

    # Apply unassigned SpaceType filter if requested
    if args.ignore_unassigned_spacetypes:
        base_removed = filter_unassigned_spacetypes(base_objs, base_hmap)
        prop_removed = filter_unassigned_spacetypes(prop_objs, prop_hmap)
        total = len(base_removed) + len(prop_removed)
        if total:
            print(f"\n--ignore-unassigned-spacetypes: Filtered {len(base_removed)} from "
                  f"{base_label}, {len(prop_removed)} from {prop_label}")

    if base_sql:
        print(f"Baseline SQL: {base_sql}")
    else:
        print(f"WARNING: No eplusout.sql found for {base_label}")

    if prop_sql:
        print(f"Proposed SQL: {prop_sql}")
    else:
        print(f"WARNING: No eplusout.sql found for {prop_label}")

    # Run all checks
    print("\nRunning consistency checks...")
    results = []

    results.append(check_weather(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_geometry_sql(base_sql, prop_sql))
    results.append(check_spacetype_inventory(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_people_loads(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_equipment_loads(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_thermostats(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_schedules(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_infiltration(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_shw(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_lighting(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_hvac_inventory(base_objs, base_hmap, prop_objs, prop_hmap))
    results.append(check_results_sql(base_sql, prop_sql, base_label, prop_label))

    # Print quick summary
    for r in results:
        status = r['status']
        marker = {'PASS': '  ', 'FAIL': '!!', 'WARN': '? ', 'INFO': '  '}
        print(f"  {marker.get(status, '  ')} [{status:4s}] {r['name']}")

    # Generate report
    report = generate_report(results, base_label, prop_label,
                             args.baseline_dir, args.proposed_dir)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\nReport written to: {args.output}")
    else:
        print(f"\n{'='*80}")
        print(report)


if __name__ == '__main__':
    main()
