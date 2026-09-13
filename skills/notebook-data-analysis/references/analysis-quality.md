# Analysis quality checklist

Use the relevant checks when designing the notebook, before execution, before marking semantic validations, and before accepting a run. Carry forward evidence for unchanged inputs and outputs; repeat affected checks when code, data, assumptions, or claims change. Required deliverables and failed or unresolved checks remain mandatory to address.

## Notebook design

Make the question, scope, input provenance, metric definitions, method, supporting evidence,
and limitations traceable to another analyst. A useful structure is question and inputs →
loading and structural checks → metric definitions and transformations → quality and assumption
checks → decision-relevant tables or charts → findings and limitations. This is an example;
adapt cell order and grouping to the analysis while preserving those outcomes and top-to-bottom
execution. Keep parameters in one `parameters`-tagged code cell when using Papermill parameters.

## Question and decision

- State the decision or question in one sentence.
- Define the population, time window, grain, units, and comparison baseline.
- Separate requested deliverables from optional exploration.
- Record any definition that could reasonably change the result.

## Input provenance

- Identify every input by stable path, query, dataset/version, or retrieval date.
- Record filters, joins, deduplication, missing-value treatment, and exclusions.
- Verify row counts, keys, types, ranges, and units before modeling or aggregation.
- State whether source data is complete, sampled, censored, delayed, or mutable.
- Do not store credentials or sensitive raw records as evidence.

## Method

- Prefer the simplest method that answers the question.
- Make parameters, random seeds, and environment-sensitive assumptions explicit.
- Check denominators and aggregation grain before comparing rates.
- Distinguish exploratory patterns from confirmatory tests.
- Quantify uncertainty when it changes the decision.
- Test plausible alternative explanations before causal language.
- Do not claim causality from descriptive evidence alone.

## Notebook evidence

- Keep the source notebook unexecuted and rerunnable from top to bottom.
- Avoid hidden state, manual output edits, and dependence on execution order.
- Use compact tables and purposeful charts instead of raw dumps.
- Give important cells stable, descriptive tags for inspection.
- Keep decision-relevant evidence durable. Export figures/tables to `artifacts/` or compact `report/` files when required deliverables or portability need them; otherwise notebook outputs and representative values in `result.md` can suffice. Link any exported files from `result.md`.
- Confirm the executed notebook contains no error outputs or unexpectedly unexecuted code cells.

## Validation fields

Review the fields recorded in `run.yaml` against evidence, not intent. Record only the three analyst-owned checks with `analysis set-validation`; the execution lifecycle owns `clean_execution`. Do not edit validation fields directly:

- `clean_execution`: Papermill finished, the notebook is structurally valid, and no error outputs remain.
- `acceptance_criteria`: the run answers the request's required scope and deliverables.
- `data_quality`: relevant quality checks passed, or limitations are strong enough that the value must be `failed`.
- `artifact_links`: every decision-relevant claim is traceable to durable notebook output and `result.md`, or to linked artifacts. A separate artifact file is not mandatory when notebook evidence is sufficient.

Use `failed` when a check fails. Do not convert uncertainty into `passed` to make the lifecycle command succeed; start another run or report the limitation.

## Interpretation

- Lead with the direct answer, then evidence.
- Report effect size and practical relevance, not only statistical significance.
- Separate facts, estimates, assumptions, and recommendations.
- Identify sensitivity to exclusions, thresholds, and definitions.
- State what the analysis cannot establish.
- Make recommended next actions specific and proportionate to evidence.

## Final synthesis

- `result.md` must describe this run without relying on unstated notebook context.
- Link its claims to durable notebook cells/outputs or selected files under `artifacts/` or `report/`.
- `output.md` must synthesize accepted evidence rather than concatenate run summaries.
- Cite accepted run IDs next to the claims they support.
- Preserve prior conclusions when superseded by explaining what changed and why.
- Keep the cumulative answer useful to a reader who does not open the notebook.
