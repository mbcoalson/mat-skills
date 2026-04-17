#!/usr/bin/env node

/**
 * validate-config.js
 *
 * Validates an envelope JSON config against the schema and engineering constraints.
 * Usage: node validate-config.js <path-to-config.json>
 *
 * Checks:
 *   - Required fields present
 *   - SI values non-null for verified items
 *   - Value ranges physically reasonable
 *   - layers[] entries have required material properties
 *   - No tbd items will be silently applied
 */

import { readFile } from 'fs/promises';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// --- Physical range checks ---
const RANGES = {
  u_factor_si: { min: 0.05, max: 10, unit: 'W/m2-K', label: 'U-factor' },
  c_factor_si: { min: 0.05, max: 10, unit: 'W/m2-K', label: 'C-factor' },
  f_factor_si: { min: 0.1, max: 5, unit: 'W/m-K', label: 'F-factor' },
  shgc: { min: 0.05, max: 0.95, unit: '', label: 'SHGC' },
  vt: { min: 0.05, max: 0.95, unit: '', label: 'VT' },
  rate_si: { min: 0.00001, max: 0.01, unit: 'm3/s-m2', label: 'Infiltration rate' },
  thickness_m: { min: 0.0001, max: 2.0, unit: 'm', label: 'Thickness' },
  conductivity: { min: 0.001, max: 500, unit: 'W/m-K', label: 'Conductivity' },
  density: { min: 1, max: 25000, unit: 'kg/m3', label: 'Density' },
  specific_heat: { min: 100, max: 10000, unit: 'J/kg-K', label: 'Specific heat' },
  thermal_resistance_si: { min: 0.01, max: 20, unit: 'm2-K/W', label: 'Thermal resistance' },
};

class ValidationResult {
  constructor() {
    this.errors = [];
    this.warnings = [];
    this.info = [];
    this.stats = { verified: 0, tbd: 0, flagged: 0, layers_checked: 0 };
  }

  error(path, msg) { this.errors.push({ path, msg }); }
  warn(path, msg) { this.warnings.push({ path, msg }); }
  log(msg) { this.info.push(msg); }

  get passed() { return this.errors.length === 0; }

  print() {
    console.log('\n' + '='.repeat(60));
    console.log(this.passed ? 'PASS - Config is valid' : 'FAIL - Config has errors');
    console.log('='.repeat(60));

    console.log(`\nStats: ${this.stats.verified} verified, ${this.stats.tbd} tbd, ${this.stats.flagged} flagged, ${this.stats.layers_checked} layers checked`);

    if (this.errors.length > 0) {
      console.log(`\nErrors (${this.errors.length}):`);
      for (const e of this.errors) {
        console.log(`  ERROR  ${e.path}: ${e.msg}`);
      }
    }

    if (this.warnings.length > 0) {
      console.log(`\nWarnings (${this.warnings.length}):`);
      for (const w of this.warnings) {
        console.log(`  WARN   ${w.path}: ${w.msg}`);
      }
    }

    if (this.info.length > 0) {
      console.log(`\nInfo:`);
      for (const i of this.info) {
        console.log(`  INFO   ${i}`);
      }
    }

    console.log('');
    return this.passed;
  }
}

function checkRange(result, path, value, rangeKey) {
  if (value == null) return;
  const r = RANGES[rangeKey];
  if (!r) return;
  if (value < r.min || value > r.max) {
    result.error(path, `${r.label} = ${value} ${r.unit} is outside reasonable range [${r.min}, ${r.max}]`);
  }
}

function checkRequired(result, obj, path, fields) {
  for (const field of fields) {
    if (!(field in obj)) {
      result.error(path, `Missing required field: ${field}`);
    }
  }
}

function checkVerifiedNotNull(result, path, obj, fields) {
  if (obj.status !== 'verified') return;
  for (const field of fields) {
    if (obj[field] == null) {
      result.error(path, `Field '${field}' is null but status is 'verified' — verified items must have values`);
    }
  }
}

function validateLayer(result, layer, path) {
  result.stats.layers_checked++;

  if (!layer.name) {
    result.error(path, 'Layer missing required field: name');
    return;
  }

  // Air gap layer — only needs thermal_resistance_si
  if (layer.thermal_resistance_si != null) {
    checkRange(result, `${path}.thermal_resistance_si`, layer.thermal_resistance_si, 'thermal_resistance_si');
    return;
  }

  // Standard layer — needs thickness, conductivity, density, specific_heat
  const stdFields = ['thickness_m', 'conductivity', 'density', 'specific_heat'];
  for (const field of stdFields) {
    if (layer[field] == null) {
      result.error(path, `Standard layer '${layer.name}' missing required property: ${field}`);
    }
  }

  checkRange(result, `${path}.thickness_m`, layer.thickness_m, 'thickness_m');
  checkRange(result, `${path}.conductivity`, layer.conductivity, 'conductivity');
  checkRange(result, `${path}.density`, layer.density, 'density');
  checkRange(result, `${path}.specific_heat`, layer.specific_heat, 'specific_heat');
}

function validateOpaqueAssembly(result, assembly, path) {
  checkRequired(result, assembly, path, ['id', 'type', 'u_factor_si', 'layers', 'apply_to', 'status']);

  // Track status
  if (assembly.status === 'verified') result.stats.verified++;
  else if (assembly.status === 'tbd') result.stats.tbd++;
  else if (assembly.status === 'flagged') result.stats.flagged++;

  checkVerifiedNotNull(result, path, assembly, ['u_factor_si']);

  if (assembly.status === 'verified') {
    checkRange(result, `${path}.u_factor_si`, assembly.u_factor_si, 'u_factor_si');

    if (!assembly.layers || assembly.layers.length === 0) {
      result.error(path, `Verified assembly '${assembly.id}' has no material layers — detailed layers required for LEED G2.2`);
    }
  }

  if (assembly.status === 'flagged' && assembly.flag) {
    result.warn(path, `Flagged: ${assembly.flag}`);
  }

  if (assembly.layers) {
    for (let i = 0; i < assembly.layers.length; i++) {
      validateLayer(result, assembly.layers[i], `${path}.layers[${i}]`);
    }
  }
}

function validateBelowGrade(result, assembly, path) {
  checkRequired(result, assembly, path, ['id', 'type', 'apply_to', 'status']);

  if (assembly.status === 'verified') {
    result.stats.verified++;
    checkVerifiedNotNull(result, path, assembly, ['c_factor_si']);
    checkRange(result, `${path}.c_factor_si`, assembly.c_factor_si, 'c_factor_si');
  } else if (assembly.status === 'tbd') {
    result.stats.tbd++;
  } else if (assembly.status === 'flagged') {
    result.stats.flagged++;
  }
}

function validateSlab(result, assembly, path) {
  checkRequired(result, assembly, path, ['id', 'type', 'apply_to', 'status']);

  if (assembly.status === 'verified') {
    result.stats.verified++;
    checkVerifiedNotNull(result, path, assembly, ['f_factor_si']);
    checkRange(result, `${path}.f_factor_si`, assembly.f_factor_si, 'f_factor_si');
  } else if (assembly.status === 'tbd') {
    result.stats.tbd++;
  } else if (assembly.status === 'flagged') {
    result.stats.flagged++;
  }
}

function validateFenestration(result, fen, path) {
  if (!fen) return;

  const vg = fen.vertical_glazing;
  if (!vg) {
    result.error(path, 'Missing required: vertical_glazing');
    return;
  }

  checkRequired(result, vg, `${path}.vertical_glazing`, ['u_factor_si', 'shgc', 'apply_to', 'status']);

  if (vg.status === 'verified') {
    result.stats.verified++;
    checkVerifiedNotNull(result, `${path}.vertical_glazing`, vg, ['u_factor_si', 'shgc']);
  } else if (vg.status === 'tbd') {
    result.stats.tbd++;
  } else if (vg.status === 'flagged') {
    result.stats.flagged++;
    if (vg.flag) result.warn(`${path}.vertical_glazing`, `Flagged: ${vg.flag}`);
  }

  checkRange(result, `${path}.vertical_glazing.u_factor_si`, vg.u_factor_si, 'u_factor_si');
  checkRange(result, `${path}.vertical_glazing.shgc`, vg.shgc, 'shgc');
  if (vg.vt != null) {
    checkRange(result, `${path}.vertical_glazing.vt`, vg.vt, 'vt');
  }

  // WWR validation
  if (fen.wwr_by_orientation) {
    const wwr = fen.wwr_by_orientation;
    for (const dir of ['north', 'east', 'south', 'west']) {
      if (wwr[dir] != null && (wwr[dir] < 0 || wwr[dir] > 1)) {
        result.error(`${path}.wwr_by_orientation.${dir}`, `WWR must be between 0 and 1, got ${wwr[dir]}`);
      }
    }
  }
}

function validateDoors(result, doors, path) {
  if (!doors) return;

  for (const type of ['swinging', 'nonswinging']) {
    const d = doors[type];
    if (!d) continue;

    checkRequired(result, d, `${path}.${type}`, ['u_factor_si', 'apply_to', 'status']);

    if (d.status === 'verified') {
      result.stats.verified++;
      checkVerifiedNotNull(result, `${path}.${type}`, d, ['u_factor_si']);
      checkRange(result, `${path}.${type}.u_factor_si`, d.u_factor_si, 'u_factor_si');

      if (d.layers) {
        for (let i = 0; i < d.layers.length; i++) {
          validateLayer(result, d.layers[i], `${path}.${type}.layers[${i}]`);
        }
      }
    } else if (d.status === 'tbd') {
      result.stats.tbd++;
    } else if (d.status === 'flagged') {
      result.stats.flagged++;
    }
  }
}

function validateInfiltration(result, inf, path) {
  if (!inf) {
    result.error(path, 'Missing required: infiltration');
    return;
  }

  checkRequired(result, inf, path, ['rate_si', 'os_method', 'apply_to', 'status']);

  if (inf.status === 'verified') {
    result.stats.verified++;
    checkVerifiedNotNull(result, path, inf, ['rate_si']);
    checkRange(result, `${path}.rate_si`, inf.rate_si, 'rate_si');
  } else if (inf.status === 'tbd') {
    result.stats.tbd++;
  } else if (inf.status === 'flagged') {
    result.stats.flagged++;
  }
}

function validateMetadata(result, meta, path) {
  checkRequired(result, meta, path, ['project_name', 'climate_zone', 'building_type', 'config_version', 'units']);

  if (meta.units && meta.units !== 'SI') {
    result.error(`${path}.units`, `Units must be 'SI', got '${meta.units}'`);
  }

  if (meta.climate_zone && !/^[1-8][A-C]?$/.test(meta.climate_zone)) {
    result.error(`${path}.climate_zone`, `Invalid climate zone format: '${meta.climate_zone}' — expected pattern like 5B, 4A, 3C`);
  }
}

function validate(config) {
  const result = new ValidationResult();

  // Top-level structure
  if (!config || typeof config !== 'object') {
    result.error('root', 'Config must be a JSON object');
    return result;
  }

  checkRequired(result, config, 'root', ['metadata', 'envelope']);

  // Metadata
  if (config.metadata) {
    validateMetadata(result, config.metadata, 'metadata');
  }

  const env = config.envelope;
  if (!env) return result;

  // Roof
  if (env.roof) {
    for (let i = 0; i < env.roof.length; i++) {
      validateOpaqueAssembly(result, env.roof[i], `envelope.roof[${i}]`);
    }
  }

  // Walls above grade
  if (env.walls_above_grade) {
    for (let i = 0; i < env.walls_above_grade.length; i++) {
      validateOpaqueAssembly(result, env.walls_above_grade[i], `envelope.walls_above_grade[${i}]`);
    }
  }

  // Walls below grade
  if (env.walls_below_grade) {
    for (let i = 0; i < env.walls_below_grade.length; i++) {
      validateBelowGrade(result, env.walls_below_grade[i], `envelope.walls_below_grade[${i}]`);
    }
  }

  // Floors
  if (env.floors) {
    for (let i = 0; i < env.floors.length; i++) {
      validateOpaqueAssembly(result, env.floors[i], `envelope.floors[${i}]`);
    }
  }

  // Slab on grade
  if (env.slab_on_grade) {
    for (let i = 0; i < env.slab_on_grade.length; i++) {
      validateSlab(result, env.slab_on_grade[i], `envelope.slab_on_grade[${i}]`);
    }
  }

  // Fenestration
  if (env.fenestration) {
    validateFenestration(result, env.fenestration, 'envelope.fenestration');
  }

  // Doors
  if (env.doors) {
    validateDoors(result, env.doors, 'envelope.doors');
  }

  // Infiltration
  validateInfiltration(result, env.infiltration, 'envelope.infiltration');

  // Summary info
  if (result.stats.tbd > 0) {
    result.log(`${result.stats.tbd} item(s) marked TBD — these will be SKIPPED by the measure`);
  }
  if (result.stats.flagged > 0) {
    result.log(`${result.stats.flagged} item(s) flagged — these will be APPLIED WITH WARNINGS`);
  }

  return result;
}

// --- Main ---
async function main() {
  const configPath = process.argv[2];
  if (!configPath) {
    console.error('Usage: node validate-config.js <path-to-config.json>');
    process.exit(1);
  }

  const fullPath = resolve(configPath);

  let raw;
  try {
    raw = await readFile(fullPath, 'utf-8');
  } catch (err) {
    console.error(`Cannot read file: ${fullPath}`);
    console.error(err.message);
    process.exit(1);
  }

  let config;
  try {
    config = JSON.parse(raw);
  } catch (err) {
    console.error(`Invalid JSON: ${err.message}`);
    process.exit(1);
  }

  console.log(`Validating: ${fullPath}`);
  const result = validate(config);
  const passed = result.print();
  process.exit(passed ? 0 : 1);
}

main();
