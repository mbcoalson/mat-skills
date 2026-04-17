# Mat's Claude Code Skills

A collection of [Claude Code](https://claude.com/claude-code) skills for building energy engineering, retro-commissioning, and eval harness work. Domain-specific skills written from a practitioner's perspective — building energy modeling, RCx analysis, ASHRAE methodology, and LLM-driven workflow engineering.

## Install

Clone the skill folder you want into your Claude Code skills directory:

```bash
# Global (available in every project)
cp -r rcx-analysis-reporting ~/.claude/skills/

# Project-local (only this project)
cp -r rcx-analysis-reporting .claude/skills/
```

Claude Code auto-discovers skills in these locations. Invoke in conversation by the skill name or by its trigger keywords.

## Skills

### Building energy modeling
- [`diagnosing-energy-models`](diagnosing-energy-models/) — OpenStudio/EnergyPlus diagnostics, geometry validation, HVAC topology, LEED Appendix G, unmet hours analysis.
- [`running-openstudio-models`](running-openstudio-models/) — OpenStudio 3.10 CLI workflow, measure application, simulation runs.
- [`writing-openstudio-model-measures`](writing-openstudio-model-measures/) — OpenStudio ModelMeasures (Ruby scripts), NREL BCL patterns.

### Building energy & performance
- [`cbecs-benchmarking`](cbecs-benchmarking/) — CBECS public dataset benchmarking.
- [`energize-denver`](energize-denver/) — Denver Article XIV building performance regulations reference.
- [`energy-efficiency`](energy-efficiency/) — Generic ASHRAE 90.1 methodology.
- [`hvac-specifications`](hvac-specifications/) — Manufacturer HVAC equipment spec lookup.

### Commissioning & RCx
- [`rcx-analysis-reporting`](rcx-analysis-reporting/) — ASHRAE Guideline 0 RCx analysis workflow (phased ECM/FIM discovery, savings quantification, report assembly).
- [`writing-oprs`](writing-oprs/) — Owner Project Requirements drafting per ASHRAE 202 / Guideline 0.

### Automation & analytics
- [`engineering-eval-harnesses`](engineering-eval-harnesses/) — Build and audit automated eval harnesses for domain plugins (generator → scorer → analyzer pattern).
- [`skyspark-analysis`](skyspark-analysis/) — SkySpark analytics, Axon queries, haystack-tagged data.

### Meta / workflow
- [`checklist-to-config`](checklist-to-config/) — Translate compliance checklists to JSON configs.
- [`skillstorming`](skillstorming/) — Brainstorm new Claude Code skills and plugins.
- [`wiki`](wiki/) — Build a persistent Obsidian-based wiki from your workspace (inspired by Andrej Karpathy's LLM Wiki approach; see [`wiki/README.md`](wiki/README.md)).

## License

MIT — see [LICENSE](LICENSE).

## Author

Mat Coalson — building energy consultant. The skills here reflect production workflows I use daily; they're shared in case they're useful to others working in the same space. No warranty, no support promises — fork, adapt, improve.
