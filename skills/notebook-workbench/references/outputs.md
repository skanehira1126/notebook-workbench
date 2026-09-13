# Saved notebook outputs

Inspect outputs without executing or editing the notebook. Resolve the requested output with
`cells list <executed.ipynb> --json`; prefer a stable cell ID or a unique existing tag.
The tags below are examples, not tags guaranteed to exist.

## Inspect executed results

Check errors before reading individual results:

```bash
notebook-workbench output errors <executed.ipynb> --json
notebook-workbench output get <executed.ipynb> --tag <result-tag> --json
```

Use text mode for a quick `text/plain`, stream, or traceback view. Use JSON for ordered MIME bundles and structured metadata. Export media when needed:

```bash
notebook-workbench output get <executed.ipynb> \
  --cell-id <id> \
  --save-media <directory> \
  --json
```

Expect deterministic PNG, JPEG, SVG, and HTML filenames. Do not interpret a successful `output errors` command with an empty list as a failure.
When `--save-media` is used, exported MIME payloads are replaced in JSON by compact descriptors containing the path, MIME type, byte count, and SHA-256 digest. Other MIME data remains available, and text mode always prints each saved media path.
