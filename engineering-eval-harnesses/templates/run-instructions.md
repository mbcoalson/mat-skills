# Run-Specific Agent Instructions (CLAUDE.md) Template
#
# This file becomes the CLAUDE.md in each overnight framework run directory.
# It tells Claude how to work within this specific eval run.
#
# Variables (replaced at scaffold time):
#   {domain}              — short domain description
#   {plugin_name}         — plugin being evaluated
#   {plugin_root}         — path to plugin directory
#   {output_format}       — DOCX, XLSX, JSON, etc.
#   {python_path}         — full path to Python executable
#   {allowed_modifications} — list of files/directories the agent CAN modify
#   {forbidden_files}     — list of files/directories the agent CANNOT modify

# {domain} — Eval Run Instructions

## Environment

- **Platform:** Windows 11, Git Bash
- **Python:** {python_path}
- **Plugin:** {plugin_root}

## What You Can Modify

Only these files/directories:
{allowed_modifications}

## What You Cannot Modify

These are off-limits — the orchestrator will reject commits that touch them:
{forbidden_files}

## Agent Behavior Rules

1. **One task per invocation.** You are either generating output or analyzing scores. Never both.
2. **Use `--allowedTools "Bash,Read,Write,Edit,Glob,Grep"`** if spawning sub-agents.
3. **Never fabricate data.** If a value cannot be extracted, use a placeholder.
4. **Commit every change** with a descriptive message following the format in the analyzer prompt.
5. **Read STATE.json first** to understand current scores and what's been tried.
6. **Read the change log** before proposing fixes to avoid repeating failed approaches.

## Self-Validation

After producing output, verify:
1. Output file exists at the expected path and is not empty
2. Expected sections/tables are present
3. No obvious errors (empty cells where data is expected, broken formatting)
4. File opens without errors

If validation fails, fix and regenerate before exiting.

## Domain-Specific Instructions

<!-- TODO: Fill in from the design brief during scaffold -->
<!-- Include: scoring dimensions, exclusions, source document locations,
     any domain-specific rules (currency formatting, heading styles, etc.) -->
