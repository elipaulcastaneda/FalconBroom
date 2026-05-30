FalconBroom Prototype — Core Components
=====================================

Goal: provide a minimal, testable prototype that demonstrates automated profiling,
cleaning, and a declarative recipe-driven workflow which an analyst can review
and tweak before applying changes.

Core components (prototype mapping):

- Connectors: for prototype we accept CSV file paths; production should add DB/S3 connectors.
- Profiling & Suggestion: `fbroom.engine.Cleaner.profile()` and `suggest_fixes()` produce simple column-level hints.
- Recipe Model: `fbroom.recipe_schema.Recipe` represents sources, cleaning steps, joins, outputs.
- Runner: `fbroom.engine.Cleaner.apply_recipe_from_spec()` applies deterministic transforms and writes snapshots.
- API: `fbroom.main` exposes endpoints for profiling, suggestions, and applying recipes; Tauri frontend can call these on dev port 3005.
- CLI: `fbroom.cli` provides quick local runs for profiling and applying recipes.

Analyst-driven workflow (how the analyst instructs the algorithm):

1. Analyst runs profiling (`/profile` or `fbroom profile --path data.csv`).
2. System returns a profile and auto-suggested cleaning steps.
3. Analyst reviews suggestions and edits a `recipe.yaml` (or uses a UI to adjust actions and parameters).
4. Analyst submits the recipe via UI/CLI (`/apply` or `fbroom apply --recipe recipe.yaml`) in dry-run mode to preview changes.
5. Analyst approves and runs full execution; outputs are written to a snapshot path and lineage is recorded.

Key UX principles for instruction:
- Make suggestions editable: every auto-suggested step is expressed in the recipe and can be toggled/parameterized.
- Provide preview/diff: show sample rows before/after and counts of affected rows.
- Add confidence scores and explainability: for fuzzy fixes, show why a suggestion was made.
- Non-destructive by default: write to new dataset paths (snapshots) until the analyst promotes outputs.
