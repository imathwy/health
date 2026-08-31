# Upstream design review

Local HealthLog independently implements its pipeline with Python's standard library. It does not vendor an upstream nutrition CLI. The following public Skills informed interface choices:

| Upstream | Reused design idea | Deliberate difference here |
|---|---|---|
| [NousResearch `fitness-nutrition`](https://github.com/NousResearch/hermes-agent/blob/main/optional-skills/health/fitness-nutrition/SKILL.md) (MIT) | USDA FoodData Central lookup, per-100-g composition, and scaling to actual grams | Lookup is optional, cached locally, emits analysis-v2 provenance, and is restricted to defensible food/preparation matches. |
| [Printing Press `pp-nutrition`](https://github.com/mvanhorn/printing-press-library/blob/main/cli-skills/pp-nutrition/SKILL.md) (Apache-2.0) | Agent JSON mode, local SQLite state, source metadata, offline cache, meal aggregation | No Node or Go dependency; `analysis.json` remains canonical and SQLite is rebuildable. Photos never leave the Mac. |
| [nutrition-analyzer](https://github.com/sickn33/agentic-awesome-skills/blob/main/skills/nutrition-analyzer/SKILL.md) (community) | Multi-day macro/micronutrient summaries and trend framing | Missing days are not zeros; fewer than five logged dates yields “data insufficient”; both estimate bounds are analyzed; correlation and causation claims are withheld without adequate paired data. |
| [SkillMe Nutrition Planner](https://github.com/SkillMedev/skills/blob/main/skills/nutrition-planner/SKILL.md) (MIT catalog) | Targets should derive from the user's weight, goals, activity, and an adjustment cadence | Targets stay in the private profile and are not silently recalculated from a generic formula during photo analysis. |

The official [USDA FoodData Central API](https://fdc.nal.usda.gov/api-guide/) is the only remote nutrition source built into the CLI. Its API key remains in the environment, and only a query string or FDC ID is sent.

## Design decisions

1. **Separate quantity evidence from composition evidence.** A photo can support a portion range while USDA supports nutrient density; neither source proves the other.
2. **Use intervals end to end.** SQLite stores lower and upper bounds, and summaries average each bound separately.
3. **Keep an audit trail.** Every schema-v2 item names its portion method, nutrition source, optional references, and notes.
4. **Treat optional nutrients as incomplete.** A missing micronutrient is unknown. It is never coerced to zero or used to diagnose deficiency.
5. **Keep local records canonical.** The database and static reports are derived artifacts and can be rebuilt.
