---
schema_version: 1
request_id: req-001
request_type: initial
created_at: {{CREATED_DATE}}
parent_request_id: null
based_on_runs: []
---

# {{TITLE}}

## Background

<!-- notebook-workbench:required request.background -->
Describe why this analysis is needed and what is currently known.

## Analysis objective

<!-- notebook-workbench:required request.objective -->
Describe the decision or understanding this analysis should support.

## Analysis questions

<!-- notebook-workbench:required request.questions -->
1. State the concrete questions the analysis must answer.

## Scope

<!-- notebook-workbench:required request.scope -->
- Population:
- Time period:
- Unit of analysis:
- Included data:
- Exclusions:

## Available information and references

<!-- notebook-workbench:required request.inputs -->
- Data sources:
- Existing notebooks or reports:
- Related definitions:

Record the inputs actually used in the run's `run.yaml`.

## Constraints and precautions

<!-- notebook-workbench:required request.constraints -->
- Data that must not be used:
- Availability or timing constraints:
- Privacy or security constraints:
- Execution constraints:

## Acceptance criteria

<!-- notebook-workbench:required request.acceptance -->
- [ ] Every analysis question is answered or explicitly marked inconclusive with a reason.
- [ ] Required data-quality checks are reported.
- [ ] Conclusions link to supporting evidence.

## Requested deliverables

List required tables, figures, exports, or presentation preferences. Leave blank when the
standard notebook, run result, artifacts, and integrated output are sufficient.
