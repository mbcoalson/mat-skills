---
name: skillstorming
description: "Use this skill when exploring ideas for new Claude Code skills or plugins before building them. Covers problem grounding with real examples, deterministic-vs-probabilistic decomposition, architecture decisions (skill vs. plugin), and producing a handoff artifact for downstream tools. Use when the user mentions designing a skill, planning a plugin, exploring an automation idea, scoping a new tool, or wants to brainstorm skill/plugin architecture — even if they just say 'I have an idea for a skill' or 'I want to automate X as a plugin.'"
---

# Skillstorming

Explore skill and plugin ideas through structured conversation, then produce a design sketch that hands off cleanly to implementation tools. This skill owns the space between "I have an idea" and "let's build it."

Not for general brainstorming — `superpowers:brainstorming` covers that. This skill has opinions: it knows you're building skills and plugins, it expects real examples as input, and it prioritizes deterministic tooling over probabilistic steps.

## Interaction Style

These rules govern the entire conversation:

- **One question at a time.** If a topic needs more exploration, break it into multiple questions across messages.
- **Multiple choice preferred.** Easier to answer than open-ended when the options are knowable.
- **2-3 approaches before committing.** Lead with your recommendation and explain why.
- **Incremental validation.** Present design in 200-300 word sections. Check after each: "Does this track?"
- **YAGNI ruthlessly.** If a feature isn't clearly needed for v1, cut it. Scope creep gets captured in Deferred Ideas, not in the design.

## Phase 1: Problem Grounding

Before exploring solutions, establish what we're solving and what we're working with.

1. **Ask for the pain point.** What's being done manually, done poorly, or not done at all? What triggered this idea?

2. **Ask for real examples.** 2-3 file paths, existing deliverables, or manual workflows that represent the current state. These are **required** — the skill does not proceed without concrete examples to analyze. If the user says "I don't have examples yet," help them identify what would serve as examples and pause until they're available.

3. **Scan for existing assets.** Read the `name` and `description` fields from skills in `.claude/skills/` and `.claude/plugins/` to find overlapping or adjacent capabilities. Surface-level only — don't dig into internals. If a description doesn't reveal the overlap, that's a signal to improve the description later, not to scan deeper now.

4. **Summarize the gap.** Present back: here's the problem, here's what already exists, here's what's missing. Get confirmation before moving on.

## Phase 2: Exploration

With the problem grounded and examples in hand, explore the solution space.

1. **Analyze the examples.** Read them. Extract patterns, common steps, decision points, and where human judgment is currently required vs. what's mechanical. This is the foundation — everything downstream builds on this analysis.

2. **Separate mechanical from judgment-heavy.** Mechanical steps become deterministic tooling (scripts, validators, parsers). Judgment-heavy steps become probabilistic skill guidance (LLM reasoning, flexible workflows). This separation directly shapes the architecture.

   The design principle: **prioritize deterministic tools and steps wherever possible.** When a step *could* be handled by either a script or LLM reasoning, default to the script. Deterministic tools are faster, more reliable, and testable. Reserve probabilistic steps for work that genuinely requires judgment — synthesis, ambiguous inputs, creative decisions.

3. **Propose 2-3 approaches.** Lead with your recommendation. For each approach, explicitly call out:
   - What's deterministic (scripts, tools, validation, parsing)
   - What's probabilistic (LLM reasoning, flexible workflows)
   - Key trade-offs

4. **Scope with Table Stakes / Differentiators / Anti-Features.** Once an approach is chosen:
   - **Table Stakes** — what must it always do? Non-negotiable baseline.
   - **Differentiators** — what makes it better than naive Claude without the skill? This is the value proposition.
   - **Anti-Features** — what should it explicitly refuse to do? Boundaries prevent scope creep.

## Phase 3: Architecture Sketch

With the approach locked, sketch the architecture.

1. **Skill or Plugin?** Apply the criteria:
   - **Skill** — single workflow, primarily guidance, light tooling (scripts in `scripts/`)
   - **Plugin** — multiple skills, hooks, handlers, shared tools, or needs its own configuration

2. **Component inventory.** What to build:
   - Scripts/tools to bundle (deterministic pieces identified in Phase 2)
   - Reference files to include
   - Existing assets to connect to or reuse (from Phase 1 scan)

3. **Present in sections.** 200-300 words each, validate after each section. Cover the architecture, component relationships, and data flow between deterministic and probabilistic pieces.

4. **Capture deferred ideas.** When good ideas surface that aren't v1, say so: "Good idea — capturing that for later." Add to the Deferred Ideas list. Don't let them inflate the current design.

5. **Produce the handoff artifact** and recommend the next step.

## Handoff Artifact

When the design is validated, write it to:
`.claude/skills/skillstorming-workspace/YYYY-MM-DD-<topic>-design.md`

Sections:
1. **Problem Statement** — what exists today, why it's painful
2. **Examples Analyzed** — file paths and key patterns extracted
3. **Table Stakes / Differentiators / Anti-Features**
4. **Proposed Architecture** — skill vs. plugin, component inventory, what to bundle vs. reference
5. **Existing Assets** — skills/plugins that connect or get reused
6. **Deferred Ideas** — good ideas parked for later
7. **Recommended Next Step** — which tool to hand off to and why

## Handoff Routing

Recommend one of these based on what was designed:

- **`skill-creator`** — single skill, primarily workflow guidance, light tooling
- **`plugin-dev:create-plugin`** — multi-component plugin with hooks, handlers, multiple skills
- **`superpowers:writing-plans`** — tool-heavy builds where TDD anchors the deterministic pieces, or complex implementations needing structured plan-then-execute
- **`gsd:new-project`** — larger multi-phase efforts needing roadmapping and milestone tracking

## Checkpoint System

When the user says "let's checkpoint" (or similar), write the current state to:
`.claude/skills/skillstorming-workspace/checkpoints/YYYY-MM-DD-<topic>.md`

Format:
```markdown
# Skillstorming: [Topic] — Checkpoint [N]
## Current Phase: [1/2/3]

## Problem
[2-3 sentences]

## Examples Analyzed
[File paths + key patterns extracted]

## Existing Assets
[Relevant skills/plugins found in scan]

## Decisions Locked
- [Decision]: [Choice] — [Why]

## Open Questions
- [What still needs exploring]

## Deferred Ideas
- [Good ideas parked for later]
```

To resume in a new session, read the latest checkpoint and pick up where it left off. No re-explaining, no lost decisions.

## Saving Next Steps

When skillstorming work is complete or paused:

```bash
node .claude/skills/work-command-center/tools/add-skill-next-steps.js \
  --skill "skillstorming" \
  --content "## Priority Tasks
1. [Current status and next action]
2. [Pending decisions or open questions]"
```
