---
schema_version: 1
request_id: {{REQUEST_ID}}
request_type: follow-up
created_at: {{CREATED_DATE}}
parent_request_id: {{PARENT_REQUEST_ID}}
based_on_runs:
{{BASED_ON_RUNS}}
---

# {{TITLE}}

## Background for the follow-up

<!-- notebook-workbench:required request.background -->
Explain which earlier finding, uncertainty, or decision motivated this request.

## Additional objective

<!-- notebook-workbench:required request.objective -->
Describe what the follow-up should clarify or change.

## Starting evidence

<!-- notebook-workbench:required request.inputs -->
Reference relevant `result.md` sections or artifacts from the runs listed above.

## Additional analysis questions

<!-- notebook-workbench:required request.questions -->
1. State the new questions.

## Changed and preserved scope

<!-- notebook-workbench:required request.scope -->
- Conditions added or changed:
- Conditions preserved from earlier requests:
- New exclusions:

## Constraints and precautions

<!-- notebook-workbench:required request.constraints -->
- Data that must not be used:
- Availability or timing constraints:
- Privacy or security constraints:
- Execution constraints:

## Acceptance criteria

<!-- notebook-workbench:required request.acceptance -->
- [ ] Every additional question is answered or explicitly marked inconclusive with a reason.
- [ ] The effect on existing conclusions is stated.
- [ ] Conclusions link to supporting evidence.

## Requested deliverables

List additional tables, figures, or exports when needed.
