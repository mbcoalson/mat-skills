---
name: writing-openstudio-model-measures
description: Write OpenStudio ModelMeasures (Ruby scripts that modify .osm files) for building energy models. Use when creating measures, writing measure.rb files, or modifying OpenStudio models programmatically. Targets OpenStudio 3.10 with best practices from NREL documentation.
---

# Writing OpenStudio Model Measures

Expert guidance for creating OpenStudio ModelMeasures - Ruby scripts that programmatically modify building energy models (.osm files).

**Target Version:** OpenStudio 3.10
**Measure Type:** ModelMeasure (modifies OSM files)
**Measure Directory:** `C:\Users\mcoalson\OpenStudio\Measures`
**Reference:** https://nrel.github.io/OpenStudio-user-documentation/reference/measure_writing_guide/

## When to Use This Skill

Invoke when you need to:
- Write a new OpenStudio measure from scratch
- Modify existing measure.rb files
- Create measure test files
- Understand OpenStudio measure structure and best practices
- Scaffold a complete measure directory with boilerplate code

## Quick Start: Creating a New Measure

Use the Node.js scaffolding script to generate a complete measure structure in your measures directory:

```bash
cd "C:\Users\mcoalson\OpenStudio\Measures"
node "C:\Users\mcoalson\Documents\WorkPath\.claude\skills\writing-openstudio-model-measures\scripts\scaffold-measure.js" "Your Measure Name"
```

This creates:
- Measure directory with snake_case name
- `measure.rb` with complete boilerplate
- `tests/` directory with MiniTest template
- `resources/` directory for helpers
- `README.md` stub

**IMPORTANT: Generate measure.xml after writing measure.rb.** The scaffold script does NOT create `measure.xml`. Without it, OpenStudio Application cannot discover the measure. After writing your measure.rb, run:

```bash
"C:/openstudio-3.10.0/bin/openstudio.exe" measure -u "C:\Users\mcoalson\OpenStudio\Measures\your_measure_name"
```

If no `measure.xml` exists yet, you must write one first (copy structure from an existing measure in the Measures directory, update uid/class_name/arguments), then run `-u` to validate and update checksums. Alternatively, use `openstudio measure new` to scaffold from scratch.

## Measure Structure

Every ModelMeasure requires:

**Required Files:**
- `measure.rb` - Main Ruby script (see [./templates/model-measure-template.rb](./templates/model-measure-template.rb))
- `measure.xml` - Metadata (**MUST exist for OpenStudio Application to discover the measure**; generate via CLI or write manually)

**Recommended Files:**
- `tests/measure_test.rb` - MiniTest unit tests (see [./templates/measure-test-template.rb](./templates/measure-test-template.rb))
- `README.md` - Documentation
- `resources/` - Helper Ruby files
- `LICENSE.md` - Distribution license

## Core Implementation Pattern

Every ModelMeasure must:

1. **Inherit from base class:**
   ```ruby
   class YourMeasure < OpenStudio::Measure::ModelMeasure
   ```

2. **Implement five required methods:**
   - `name()` - User-visible title
   - `description()` - General audience explanation
   - `modeler_description()` - Technical details for modelers
   - `arguments(model)` - Define user inputs
   - `run(model, runner, user_arguments)` - Main logic

3. **Return boolean from run():**
   - `true` if measure succeeds
   - `false` if measure fails (after logging error)

## Required Methods Details

### 1. name()
```ruby
def name
  return "Add Window Overhangs"
end
```
- Short, descriptive, general
- User-visible in OpenStudio Application

### 2. description()
```ruby
def description
  return "Adds overhangs to all exterior windows based on user-specified projection factor. Improves solar control and reduces cooling loads."
end
```
- General audience explanation
- Include what measure does and benefits

### 3. modeler_description()
```ruby
def modeler_description
  return "Iterates through all SubSurfaces with 'Window' type, creates ShadingSurfaceGroup and ShadingSurface objects with projection calculated as window height × projection factor. Assumes windows face outward from building."
end
```
- Technical implementation details
- Assumptions, algorithms, references

### 4. arguments(model)
```ruby
def arguments(model)
  args = OpenStudio::Measure::OSArgumentVector.new

  # Double argument
  projection_factor = OpenStudio::Measure::OSArgument.makeDoubleArgument('projection_factor', true)
  projection_factor.setDisplayName('Projection Factor')
  projection_factor.setDescription('Overhang depth as multiple of window height')
  projection_factor.setDefaultValue(0.5)
  args << projection_factor

  # Boolean argument
  apply_to_north = OpenStudio::Measure::OSArgument.makeBoolArgument('apply_to_north', true)
  apply_to_north.setDisplayName('Apply to North-Facing Windows?')
  apply_to_north.setDefaultValue(false)
  args << apply_to_north

  # Choice argument
  choices = OpenStudio::StringVector.new
  choices << "All Windows"
  choices << "South-Facing Only"
  choices << "East and West Only"
  orientation = OpenStudio::Measure::OSArgument.makeChoiceArgument('orientation', choices, true)
  orientation.setDisplayName('Window Orientation Filter')
  orientation.setDefaultValue("All Windows")
  args << orientation

  return args
end
```

**Argument Types:**
- `makeDoubleArgument(name, required)` - Real numbers
- `makeIntegerArgument(name, required)` - Whole numbers
- `makeBoolArgument(name, required)` - True/false
- `makeStringArgument(name, required)` - Text input
- `makeChoiceArgument(name, choices_vector, required)` - Dropdown selection

### 5. run(model, runner, user_arguments)

```ruby
def run(model, runner, user_arguments)
  super(model, runner, user_arguments)

  # 1. Validate and extract arguments
  if !runner.validateUserArguments(arguments(model), user_arguments)
    return false
  end

  projection_factor = runner.getDoubleArgumentValue('projection_factor', user_arguments)
  apply_to_north = runner.getBoolArgumentValue('apply_to_north', user_arguments)
  orientation = runner.getStringArgumentValue('orientation', user_arguments)

  # 2. Additional validation
  if projection_factor < 0 || projection_factor > 5
    runner.registerError("Projection factor must be between 0 and 5, got #{projection_factor}")
    return false
  end

  # 3. Register initial condition
  initial_overhang_count = model.getShadingSurfaces.length
  runner.registerInitialCondition("Model has #{initial_overhang_count} shading surfaces.")

  # 4. Main measure logic
  windows_modified = 0

  model.getSubSurfaces.each do |sub_surface|
    next unless sub_surface.subSurfaceType == "FixedWindow" || sub_surface.subSurfaceType == "OperableWindow"

    # Your logic here
    windows_modified += 1
    runner.registerInfo("Added overhang to window: #{sub_surface.name}")
  end

  # 5. Handle not applicable case
  if windows_modified == 0
    runner.registerAsNotApplicable("No windows found in model.")
    return true
  end

  # 6. Register final condition
  final_overhang_count = model.getShadingSurfaces.length
  runner.registerFinalCondition("Added #{final_overhang_count - initial_overhang_count} overhangs to #{windows_modified} windows.")

  return true
end
```

## Logging and User Communication

Use `runner` methods to communicate:

**Info Messages** (measure continues):
```ruby
runner.registerInfo("Processing 42 windows...")
```

**Warning Messages** (measure continues):
```ruby
runner.registerWarning("Window '#{name}' has no parent surface, skipping.")
```

**Error Messages** (measure stops):
```ruby
runner.registerError("Invalid projection factor: #{value}")
return false
```

**Condition Tracking:**
```ruby
runner.registerInitialCondition("Before: #{count} objects")
runner.registerFinalCondition("After: #{count} objects, added #{delta}")
```

**Not Applicable:**
```ruby
runner.registerAsNotApplicable("No HVAC systems found in model.")
return true  # Still return true!
```

## OpenStudio API Patterns

### Getting Objects from Model

**Non-unique objects** (can have multiple instances):
```ruby
# Get all instances
spaces = model.getSpaces
thermal_zones = model.getThermalZones

# Get by name
space = model.getSpaceByName("Office 101")
if space.is_initialized
  space_obj = space.get
  # Use space_obj
end
```

**Unique objects** (only one instance):
```ruby
# Get the single instance
building = model.getBuilding
site = model.getSite
```

### Safe Optional Handling

OpenStudio uses `boost::optional` types extensively. **Always check before using `.get()`:**

```ruby
# UNSAFE - will crash if empty
zone = space.thermalZone.get  # BAD!

# SAFE - check first
if !space.thermalZone.empty?
  zone = space.thermalZone.get
  runner.registerInfo("Space is in zone: #{zone.name}")
else
  runner.registerWarning("Space has no thermal zone assigned.")
end

# Alternative safe pattern
if space.thermalZone.is_initialized
  zone = space.thermalZone.get
  # Use zone
end
```

### Common Model Queries

```ruby
# Spaces and Zones
model.getSpaces.each do |space|
  puts space.name
end

model.getThermalZones.each do |zone|
  puts zone.name
end

# Surfaces
model.getSurfaces.each do |surface|
  puts "#{surface.name}: #{surface.surfaceType}"
end

# SubSurfaces (windows, doors)
model.getSubSurfaces.each do |sub_surface|
  puts "#{sub_surface.name}: #{sub_surface.subSurfaceType}"
end

# HVAC Equipment
model.getAirLoopHVACs.each do |air_loop|
  puts air_loop.name
end

model.getPlantLoops.each do |plant_loop|
  puts plant_loop.name
end

# Constructions and Materials
model.getConstructions.each do |construction|
  puts construction.name
end

# Schedules
model.getScheduleRulesets.each do |schedule|
  puts schedule.name
end
```

## Testing with MiniTest

Every measure should include tests in `tests/measure_test.rb`.

**Run tests:**
```bash
cd /path/to/your_measure
ruby tests/measure_test.rb
```

See [./templates/measure-test-template.rb](./templates/measure-test-template.rb) for complete test structure.

**Basic test pattern:**
```ruby
def test_valid_arguments
  # Load test model
  model = load_test_model

  # Create measure instance
  measure = YourMeasure.new

  # Get arguments
  arguments = measure.arguments(model)

  # Set argument values
  argument_map = OpenStudio::Measure.convertOSArgumentVectorToMap(arguments)
  projection_factor = arguments[0].clone
  assert(projection_factor.setValue(0.5))
  argument_map['projection_factor'] = projection_factor

  # Run measure
  measure.run(model, runner, argument_map)
  result = runner.result

  # Assertions
  assert_equal('Success', result.value.valueName)
  assert(result.info.size > 0)
  assert_equal(0, result.warnings.size)
end
```

## Best Practices

### Code Quality
- Follow [Ruby Style Guide](https://github.com/bbatsov/ruby-style-guide)
- Use 2-space indentation
- Keep methods focused and concise
- Add comments for complex logic

### Input Validation
- Always validate user arguments
- Check value ranges and logical constraints
- Provide clear error messages
- Use `runner.validateUserArguments()` first

### Error Handling
- Check for empty optionals before calling `.get()`
- Handle edge cases (empty model, missing objects)
- Use `registerAsNotApplicable()` when measure doesn't apply
- Return `false` only after logging error with `registerError()`

### Performance
- Avoid nested loops when possible
- Cache frequently accessed values
- Use `model.getObjectsByType()` for specific object types

### User Experience
- Use clear, descriptive argument names
- Set sensible default values
- Log progress for long-running operations
- Report initial and final conditions

## OpenStudio 3.10 Notes

**Ruby Version:** OpenStudio 3.10 uses Ruby 2.7.2
**CLI Path:** `C:/openstudio-3.10.0/bin/openstudio.exe`
**API Documentation:** https://openstudio-sdk-documentation.s3.amazonaws.com/index.html

**Common gotchas:**
- Method names are camelCase (OpenStudio convention), not snake_case (Ruby convention)
- Units matter - always check if arguments need IP or SI units
- `measure.xml` MUST exist for OpenStudio Application to discover the measure — it does NOT auto-generate from measure.rb alone
- `openstudio measure -u` updates an existing measure.xml (checksums, version) but CANNOT create one from scratch
- To create measure.xml from scratch: write it manually (copy from existing measure) or use `openstudio measure new`
- Test with multiple model types (residential, commercial, different HVAC systems)

## Common Measure Patterns

### Iterating Through Spaces
```ruby
model.getSpaces.each do |space|
  runner.registerInfo("Processing space: #{space.name}")

  # Get space properties
  floor_area = space.floorArea  # m²

  # Get thermal zone
  if space.thermalZone.is_initialized
    zone = space.thermalZone.get
    runner.registerInfo("  Zone: #{zone.name}")
  end

  # Get space type
  if space.spaceType.is_initialized
    space_type = space.spaceType.get
    runner.registerInfo("  Type: #{space_type.name}")
  end
end
```

### Modifying Constructions
```ruby
model.getSurfaces.each do |surface|
  next unless surface.surfaceType == "RoofCeiling"
  next unless surface.outsideBoundaryCondition == "Outdoors"

  # Create or get construction
  new_construction = model.getConstructionByName("High Performance Roof")
  if new_construction.is_initialized
    surface.setConstruction(new_construction.get)
    runner.registerInfo("Updated roof construction: #{surface.name}")
  end
end
```

### Working with Schedules
```ruby
# Get schedule by name
schedule = model.getScheduleRulesetByName("Office Occupancy")
if schedule.is_initialized
  sched = schedule.get
  # Modify schedule rules
end

# Create new schedule
new_schedule = OpenStudio::Model::ScheduleRuleset.new(model)
new_schedule.setName("Custom Schedule")
new_schedule.defaultDaySchedule.setName("Custom Default")
# Add time-value pairs
new_schedule.defaultDaySchedule.addValue(OpenStudio::Time.new(0,8,0,0), 0)
new_schedule.defaultDaySchedule.addValue(OpenStudio::Time.new(0,18,0,0), 1)
```

## Additional Resources

**Templates:**
- [./templates/model-measure-template.rb](./templates/model-measure-template.rb) - Complete ModelMeasure boilerplate
- [./templates/measure-test-template.rb](./templates/measure-test-template.rb) - MiniTest template

**Scripts:**
- [./scripts/scaffold-measure.js](./scripts/scaffold-measure.js) - Node.js script to create measure directory structure

**External References:**
- [NREL Measure Writing Guide](https://nrel.github.io/OpenStudio-user-documentation/reference/measure_writing_guide/)
- [OpenStudio API Documentation](https://openstudio-sdk-documentation.s3.amazonaws.com/index.html)
- [Ruby Style Guide](https://github.com/bbatsov/ruby-style-guide)
- [OpenStudio Common Measures](https://github.com/NREL/openstudio-common-measures-gem) - Example implementations

## Workflow Summary

1. **Scaffold measure** using Node.js script or manually create directory
2. **Write measure.rb** using template as starting point
3. **Implement five required methods** (name, description, modeler_description, arguments, run)
4. **Add input validation** and error handling
5. **Write measure.xml** — copy from an existing measure, update uid/class_name/arguments/description
6. **Run CLI to validate** — `openstudio measure -u <measure_dir>` validates Ruby and updates checksums
7. **Test with MiniTest** — create tests in `tests/` directory
8. **Open in OpenStudio Application** — measure appears under its taxonomy tag
9. **Iterate and refine** based on testing with various models

## Critical Corrections

### measure.xml is REQUIRED (not auto-generated)
OpenStudio Application cannot discover a measure without `measure.xml`. The scaffold script does NOT create it. The CLI `-u` flag updates an existing XML but cannot create one. You MUST either:
- Write `measure.xml` manually (copy structure from existing measure in Measures directory)
- Use `openstudio measure new` to scaffold from scratch
Then run `openstudio measure -u` to validate and update checksums.
(Learned: 2026-02-17)

### OpenStudio CLI path
`C:/openstudio-3.10.0/bin/openstudio.exe` — always verify version before running CLI commands.
(Learned: 2026-02-17)

### WaterHeaterMixed has only 3 fuel type setters
`WaterHeaterMixed` does NOT have `setOffCycleFuelType` or `setOnCycleFuelType` methods. These don't exist in the EnergyPlus IDD or the OpenStudio SDK. The only fuel type setters are:
- `setHeaterFuelType('NaturalGas')` — main heater fuel
- `setOffCycleParasiticFuelType('Electricity')` — off-cycle parasitic loads
- `setOnCycleParasiticFuelType('Electricity')` — on-cycle parasitic loads

Off/on-cycle standby losses use the heater fuel implicitly — there is no separate fuel type field.
(Learned: 2026-02-17)

### HeatExchangerAirToAirSensibleAndLatent — at75 methods deprecated in OS 3.8+
The `setSensibleEffectivenessat75HeatingAirFlow`, `setLatentEffectivenessat75HeatingAirFlow`, `setSensibleEffectivenessat75CoolingAirFlow`, and `setLatentEffectivenessat75CoolingAirFlow` methods are **deprecated as of OpenStudio 3.8.0**. They emit warnings and will eventually be removed.

**Replace with curve-based API:**
```ruby
# Keep the at-100% setters (still valid)
erv.setSensibleEffectivenessat100HeatingAirFlow(0.50)
erv.setLatentEffectivenessat100HeatingAirFlow(0.50)
erv.setSensibleEffectivenessat100CoolingAirFlow(0.50)
erv.setLatentEffectivenessat100CoolingAirFlow(0.50)

# For constant effectiveness at all airflow ratios, use a flat CurveLinear
eff_curve = OpenStudio::Model::CurveLinear.new(model)
eff_curve.setName('ERV Constant Effectiveness Ratio')
eff_curve.setCoefficient1Constant(1.0)  # y = 1.0 (flat multiplier)
eff_curve.setCoefficient2x(0.0)
eff_curve.setMinimumValueofx(0.0)
eff_curve.setMaximumValueofx(1.0)

# Apply curve-based effectiveness (OS 3.8+ API)
erv.setSensibleEffectivenessofHeatingAirFlowCurve(eff_curve)
erv.setLatentEffectivenessofHeatingAirFlowCurve(eff_curve)
erv.setSensibleEffectivenessofCoolingAirFlowCurve(eff_curve)
erv.setLatentEffectivenessofCoolingAirFlowCurve(eff_curve)
```

The curve represents a multiplier on the at-100% effectiveness as a function of airflow ratio. A flat curve at 1.0 means constant effectiveness at all part-load ratios.
(Learned: 2026-02-17)

---

**Last Updated:** 2026-02-17
**Target OpenStudio Version:** 3.10.0
**Ruby Version:** 2.7.2


## Saving Next Steps

When writing-openstudio-model-measures work is complete or paused:

```bash
node .claude/skills/work-command-center/tools/add-skill-next-steps.js \
  --skill "writing-openstudio-model-measures" \
  --content "## Priority Tasks
1. Complete custom ModelMeasure implementation
2. Test measure with sample .osm file
3. Document measure arguments and usage"
```

See: `.claude/skills/work-command-center/skill-next-steps-convention.md`
