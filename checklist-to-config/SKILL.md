---
name: checklist-to-config
description: Use this skill when translating an energy code compliance checklist into a machine-readable JSON config for OpenStudio model measures. This includes interviewing the user about material layers from DD drawings, converting IP to SI units, performing gap analysis on checklist data, generating envelope configs, and validating config files. Triggers on checklist-to-config, compliance config, envelope config, material layers, config interview.
---

# Checklist-to-Config

Reads energy code compliance checklists and produces validated JSON configuration files through collaborative dialogue with the user. The config files drive OpenStudio measures that apply properties to building energy models.

**Current Scope:** v1 Envelope only (roof, walls, floors, fenestration, doors, infiltration)

## When to Use This Skill

Invoke when:
- Translating a compliance checklist into model-ready properties
- Building a JSON config for `apply_envelope_from_config` or future measures
- The user has a compliance checklist and DD drawings and needs to populate material layers
- Resuming a partially-completed config interview

## Interactive Interview Process

Follow these five steps in order. One question at a time. Multiple choice preferred.

### Step 1: Intake

1. Read the compliance checklist file provided by the user
2. Read the JSON schema to understand every required field:
   ```
   .claude/skills/checklist-to-config/schemas/envelope-config.schema.json
   ```
3. Build a gaps list: which fields have values in the checklist vs. which are missing

### Step 2: Triage

Group every config field into three categories and present the summary to the user:

- **Known** - Values clearly stated in the checklist (e.g., "Roof R-31.25, U-0.032"). These will be auto-populated.
- **Findable** - Values not in the checklist but likely in specific documents. Recommend where to look:
  - "Check sheet A-501 for the roof detail section"
  - "The door schedule is typically on the architectural set"
  - "Wall section details are usually on structural sheets S-XXX"
- **Unknown** - Genuinely missing, needs design team input or a default assumption

Present the triage table and ask the user to confirm before proceeding.

### Step 3: Interview (Assembly by Assembly)

Walk through each envelope assembly one at a time in this order:
1. Roof
2. Walls above grade
3. Walls below grade
4. Floors
5. Slab on grade
6. Fenestration (vertical glazing)
7. Doors (swinging, nonswinging)
8. Infiltration

For each assembly:

1. **Present what's known** from the checklist (R-values, U-factors, types)
2. **Ask for material layer details** from the DD set. Offer common assemblies as multiple choice:

   > **Roof assembly.** The checklist shows IEAD roof at R-31.25 (U-0.032). What materials make up this assembly?
   >
   > 1. **TPO/EPDM membrane + polyiso + metal deck** (most common for commercial)
   > 2. **Built-up roof + polyiso + concrete deck**
   > 3. **I have the roof detail — let me describe it**
   > 4. **Not sure yet — use a typical assembly**

3. **For each layer**, collect or confirm:
   - Material name
   - Thickness (accept IP, convert to SI)
   - Conductivity (W/m-K)
   - Density (kg/m3)
   - Specific heat (J/kg-K)
   - For air gaps: thermal resistance (m2-K/W) instead of layer properties

4. **For Findable gaps**, recommend specific places to look:
   - "This is usually in the wall section detail on sheet A-XXX"
   - "Check the door schedule for the U-factor"

5. **For Unknown gaps**, offer smart defaults with engineering justification:
   - "No VT specified. For SHGC 0.33, typical VT is 0.42-0.50. Use 0.45 as placeholder?"
   - "Typical steel stud + R-19 batt has conductivity ~0.049 W/m-K"
   - Mark defaulted items as `"status": "flagged"` with a note explaining the assumption

6. **Handle TBD items**: If the user doesn't have the information and doesn't want a default, set `"status": "tbd"` with a descriptive note. The measure will skip these.

### Step 4: Validation (Per Assembly)

After completing each assembly, show what will go into the config:

```
Roof Assembly Summary:
  ID: roof_iead
  Type: IEAD
  U-factor: 0.182 W/m2-K (0.032 Btu/h-ft2-F)
  Layers (outside to inside):
    1. TPO Membrane — 3mm, k=0.17, rho=1400, cp=900
    2. Polyiso Insulation — 127mm, k=0.024, rho=32, cp=1470
    3. Metal Deck — 2mm, k=45.0, rho=7800, cp=500
  Status: verified
  Apply to: all exterior roofs

Does this look correct? [Yes / Edit a layer / Add a layer / Start over]
```

### Step 5: Output

1. Assemble the complete JSON config with all assemblies
2. Populate the metadata section (project name, climate zone, date, etc.)
3. Save to the project configs directory:
   ```
   User-Files/work-tracking/projects/{project}/configs/{project}_envelope_v1.json
   ```
4. Run validation:
   ```bash
   node .claude/skills/checklist-to-config/tools/validate-config.js <config-path>
   ```
5. Report validation results to user
6. If validation fails, walk through errors and fix interactively

## Unit Conversion Reference

All config values are stored in **SI units**. During the interview, accept IP values and convert:

| Property | IP Unit | SI Unit | Conversion |
|----------|---------|---------|------------|
| U-factor | Btu/h-ft2-F | W/m2-K | × 5.678 |
| R-value | h-ft2-F/Btu | m2-K/W | × 0.1761 |
| Thickness | inches | meters | × 0.0254 |
| Conductivity | Btu-in/h-ft2-F | W/m-K | × 0.1442 |
| Density | lb/ft3 | kg/m3 | × 16.018 |
| Specific heat | Btu/lb-F | J/kg-K | × 4186.8 |
| Infiltration | cfm/ft2 | m3/s-m2 | × 0.00508 |
| C-factor | Btu/h-ft2-F | W/m2-K | × 5.678 |
| F-factor | Btu/h-ft-F | W/m-K | × 1.731 |

## Config Schema

The JSON schema defines the complete structure:
```
.claude/skills/checklist-to-config/schemas/envelope-config.schema.json
```

Key design principles:
- **SI units** for all thermal values (what OpenStudio consumes)
- **IP equivalents** stored as metadata for human readability
- **`status` field** on each item: `verified`, `tbd`, or `flagged`
- **`layers[]` array** for opaque constructions with full material properties
- **Nullable fields** — `null` means "not yet known"
- **No massless materials** — detailed layers required for LEED G2.2 compliance

## Validation Tool

```bash
node .claude/skills/checklist-to-config/tools/validate-config.js <path-to-config.json>
```

Checks:
- All required fields present per schema
- SI values non-null for `verified` items
- Value ranges physically reasonable
- Every `layers[]` entry has required material properties
- No `tbd` items will be silently applied

## Status Field Rules

| Status | Meaning | Measure Behavior |
|--------|---------|-----------------|
| `verified` | Value confirmed by user or checklist | Apply to model |
| `tbd` | Not yet known | Skip — do not apply |
| `flagged` | Value has known issue or uses default | Apply with warning logged |

## Resuming a Partial Interview

If context overflows or the session ends mid-interview:
1. Check for existing config file in the configs directory
2. Read it to see which assemblies are complete
3. Resume from the next incomplete assembly
4. The config file is the durable artifact — everything answered so far is preserved

## Integration

This skill produces configs consumed by:
- `apply_envelope_from_config` measure (v1 — envelope)
- Future: `apply_loads_from_config`, `apply_hvac_from_config`, `apply_swh_from_config`

After config generation, the workflow continues with:
1. `writing-openstudio-model-measures` — writes the measure if not yet written
2. `running-openstudio-models` — applies measure and runs simulation
3. `energyplus-assistant` — QA/QC validation of results
