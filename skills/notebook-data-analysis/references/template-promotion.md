# Notebook template promotion

Promote a completed source notebook to a reusable template only after the analysis is closed and the pattern has demonstrated reuse value.

## Promote when

- The analytical question recurs with different inputs or parameters.
- The notebook has a stable top-to-bottom structure and a single clear parameter cell.
- Domain-specific logic is intentional and documented.
- Data access and environment requirements can be stated without embedding credentials.
- At least one clean run proves the template structure executes successfully.

## Do not promote when

- The notebook is a one-off investigation or still contains speculative branches.
- Reuse would require editing many code cells rather than supplying parameters.
- Paths, dates, schemas, or identifiers are hard-coded to one incident.
- Outputs, Papermill metadata, local caches, or sensitive data remain embedded.
- Important quality checks exist only as analyst memory.

## Promotion procedure

1. Copy the completed run's `analysis.ipynb`; never move or mutate the evidence run.
2. Remove outputs, execution counts, transient Papermill metadata, local paths, and secrets.
3. Consolidate user-controlled values into exactly one tagged `parameters` cell.
4. Keep provenance, data-quality, method, visualization, and conclusion sections explicit.
5. Replace incident-specific prose with instructions and safe defaults.
6. Validate with `notebook-workbench validate TEMPLATE.ipynb --source`.
7. Start a fresh analysis run with `--notebook-template TEMPLATE.ipynb` and execute it with representative parameters.
8. Document the template's intended scope, inputs, outputs, limitations, and owner near the template.

Promote only when requested or included in the agreed scope; a request to build the template already authorizes routine preparation and validation. A successful analysis alone does not authorize adding a template deliverable or establish reuse value.
