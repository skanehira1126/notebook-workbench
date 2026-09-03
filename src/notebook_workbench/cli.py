"""Command-line interface for Notebook Workbench."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .analysis_ops import (
    accept_analysis_run,
    add_analysis_request,
    complete_analysis_run,
    execute_analysis_run,
    initialize_analysis,
    recover_analysis_run,
    set_analysis_status,
    set_run_validation,
    start_analysis_run,
    validate_analysis_workspace,
)
from .errors import (
    AnalysisValidationError,
    CLIUsageError,
    NotebookValidationError,
    WorkbenchError,
    WorkbenchIOError,
)
from .execution_runners import DEFAULT_DOCKER_IMAGE
from .notebook_ops import (
    AddCellRequest,
    CellSelector,
    CreateNotebookRequest,
    RemoveCellRequest,
    ReplaceCellRequest,
    TagMutationRequest,
    add_cell,
    create_notebook,
    get_cell,
    get_script,
    list_cells,
    remove_cell,
    replace_cell,
    update_tags,
    validate_notebook,
)
from .output_ops import get_outputs, list_errors, render_outputs_text


class WorkbenchArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = WorkbenchArgumentParser(prog="notebook-workbench")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    groups = parser.add_subparsers(dest="group", required=True)

    notebook = groups.add_parser("notebook", help="create a notebook")
    notebook_commands = notebook.add_subparsers(dest="command", required=True)
    notebook_create = notebook_commands.add_parser("create", help="create a notebook")
    notebook_create.add_argument("notebook", type=Path)
    notebook_create.add_argument("--kernel-name", default="python3")
    notebook_create.add_argument("--kernel-display-name", default="Python 3")
    notebook_create.add_argument("--language", default="python")
    _add_json(notebook_create)

    cells = groups.add_parser("cells", help="inspect notebook cells")
    cells_commands = cells.add_subparsers(dest="command", required=True)
    cells_list = cells_commands.add_parser("list", help="list cells")
    cells_list.add_argument("notebook", type=Path)
    _add_json(cells_list)

    cell = groups.add_parser("cell", help="get or mutate a cell")
    cell_commands = cell.add_subparsers(dest="command", required=True)
    cell_get = cell_commands.add_parser("get", help="get cell source")
    cell_get.add_argument("notebook", type=Path)
    _add_selector(cell_get, allow_index=True)
    _add_json(cell_get)

    cell_add = cell_commands.add_parser("add", help="add a cell")
    cell_add.add_argument("notebook", type=Path)
    cell_add.add_argument("--type", dest="cell_type", choices=["code", "markdown", "raw"], required=True)
    cell_add.add_argument("--tag", action="append", default=[])
    cell_add.add_argument("--before-tag")
    cell_add.add_argument("--after-tag")
    cell_add.add_argument("--before-cell-id")
    cell_add.add_argument("--after-cell-id")
    cell_add.add_argument("--append", action="store_true")
    _add_source(cell_add)
    _add_mutation_common(cell_add)

    cell_replace = cell_commands.add_parser("replace", help="replace cell source")
    cell_replace.add_argument("notebook", type=Path)
    _add_selector(cell_replace)
    _add_source(cell_replace)
    _add_mutation_common(cell_replace)

    cell_remove = cell_commands.add_parser("remove", help="remove a cell")
    cell_remove.add_argument("notebook", type=Path)
    _add_selector(cell_remove)
    cell_remove.add_argument("--allow-parameters-cell", action="store_true")
    _add_mutation_common(cell_remove)

    tag = groups.add_parser("tag", help="mutate cell tags")
    tag_commands = tag.add_subparsers(dest="command", required=True)
    for operation in ("add", "remove", "set"):
        command = tag_commands.add_parser(operation, help=f"{operation} cell tags")
        command.add_argument("notebook", type=Path)
        _add_selector(command, tag_option="--cell-tag")
        command.add_argument("--tag", action="append", required=True)
        _add_mutation_common(command)
    rename = tag_commands.add_parser("rename", help="rename a tag")
    rename.add_argument("notebook", type=Path)
    rename.add_argument("--from", dest="from_tag", required=True)
    rename.add_argument("--to", dest="to_tag", required=True)
    rename.add_argument("--all", action="store_true")
    _add_mutation_common(rename)

    script = groups.add_parser("script", help="render percent script")
    script_commands = script.add_subparsers(dest="command", required=True)
    script_get = script_commands.add_parser("get", help="get percent script")
    script_get.add_argument("notebook", type=Path)
    _add_selector(script_get, allow_index=True, required=False)
    script_get.add_argument("--include-markdown", action="store_true")

    output = groups.add_parser("output", help="inspect executed outputs")
    output_commands = output.add_subparsers(dest="command", required=True)
    output_get = output_commands.add_parser("get", help="get cell outputs")
    output_get.add_argument("notebook", type=Path)
    _add_selector(output_get, allow_index=True)
    output_get.add_argument("--save-media", type=Path)
    _add_json(output_get)
    output_errors = output_commands.add_parser("errors", help="list error outputs")
    output_errors.add_argument("notebook", type=Path)
    _add_json(output_errors)

    validate = groups.add_parser("validate", help="validate a notebook")
    validate.add_argument("notebook", type=Path)
    mode = validate.add_mutually_exclusive_group()
    mode.add_argument("--source", action="store_true")
    mode.add_argument("--executed", action="store_true")
    _add_json(validate)

    analysis = groups.add_parser("analysis", help="manage reproducible analysis workspaces")
    analysis_commands = analysis.add_subparsers(dest="command", required=True)

    analysis_init = analysis_commands.add_parser("init", help="create an analysis workspace")
    analysis_init.add_argument("--root", type=Path, default=Path("notebooks/analyses"))
    analysis_init.add_argument("--analysis-id", required=True)
    analysis_init.add_argument("--title", required=True)
    _add_json(analysis_init)

    analysis_request = analysis_commands.add_parser(
        "add-request", help="add an immutable follow-up request"
    )
    analysis_request.add_argument("--analysis-dir", type=Path, required=True)
    analysis_request.add_argument("--title", required=True)
    analysis_request.add_argument("--parent-request", required=True)
    analysis_request.add_argument("--based-on-run", action="append", required=True)
    _add_json(analysis_request)

    analysis_start = analysis_commands.add_parser("start-run", help="create a planned run")
    analysis_start.add_argument("--analysis-dir", type=Path, required=True)
    analysis_start.add_argument("--request-id", required=True)
    analysis_start.add_argument("--title")
    analysis_start.add_argument("--notebook-template", type=Path)
    _add_json(analysis_start)

    analysis_execute = analysis_commands.add_parser(
        "execute", help="execute a planned run with Papermill"
    )
    analysis_execute.add_argument("--analysis-dir", type=Path, required=True)
    analysis_execute.add_argument("--run-id", required=True)
    analysis_execute.add_argument("--parameters-file", type=Path)
    analysis_execute.add_argument("--kernel")
    analysis_execute.add_argument("--cwd", type=Path)
    analysis_execute.add_argument("--start-timeout", type=int, default=60)
    analysis_execute.add_argument("--execution-timeout", type=int)
    analysis_execute.add_argument(
        "--runner", choices=["local", "docker"], default="local"
    )
    analysis_execute.add_argument(
        "--project-root", type=Path, help="project containing pyproject.toml and uv.lock"
    )
    analysis_execute.add_argument(
        "--docker-image",
        help=f"Docker image (default: {DEFAULT_DOCKER_IMAGE})",
    )
    analysis_execute.add_argument("--memory", help="required Docker memory limit")
    analysis_execute.add_argument(
        "--memory-swap", help="Docker memory plus swap limit (defaults to --memory)"
    )
    analysis_execute.add_argument(
        "--mount",
        dest="mounts",
        action="append",
        help="repeatable SOURCE:TARGET[:ro|rw] bind mount (default: ro)",
    )
    analysis_execute.add_argument(
        "--env",
        dest="environment_variables",
        action="append",
        help="repeatable KEY=VALUE container environment variable",
    )
    _add_json(analysis_execute)

    analysis_validation = analysis_commands.add_parser(
        "set-validation", help="record a semantic validation result"
    )
    analysis_validation.add_argument("--analysis-dir", type=Path, required=True)
    analysis_validation.add_argument("--run-id", required=True)
    analysis_validation.add_argument(
        "--check",
        choices=["acceptance_criteria", "data_quality", "artifact_links"],
        required=True,
    )
    analysis_validation.add_argument(
        "--result", choices=["passed", "failed"], required=True
    )
    _add_json(analysis_validation)

    analysis_recover = analysis_commands.add_parser(
        "recover-run", help="mark an interrupted running run as failed"
    )
    analysis_recover.add_argument("--analysis-dir", type=Path, required=True)
    analysis_recover.add_argument("--run-id", required=True)
    analysis_recover.add_argument("--reason", required=True)
    _add_json(analysis_recover)

    analysis_complete = analysis_commands.add_parser(
        "complete-run", help="complete a validated executed run"
    )
    analysis_complete.add_argument("--analysis-dir", type=Path, required=True)
    analysis_complete.add_argument("--run-id", required=True)
    _add_json(analysis_complete)

    analysis_accept = analysis_commands.add_parser(
        "accept-run", help="accept a completed run into output.md"
    )
    analysis_accept.add_argument("--analysis-dir", type=Path, required=True)
    analysis_accept.add_argument("--run-id", required=True)
    _add_json(analysis_accept)

    analysis_status = analysis_commands.add_parser(
        "set-status", help="complete or archive an analysis"
    )
    analysis_status.add_argument("--analysis-dir", type=Path, required=True)
    analysis_status.add_argument(
        "--status", choices=["completed", "archived"], required=True
    )
    _add_json(analysis_status)

    analysis_validate = analysis_commands.add_parser(
        "validate", help="validate an analysis workspace"
    )
    analysis_validate.add_argument("--analysis-dir", type=Path, required=True)
    analysis_validate.add_argument("--portable", action="store_true")
    _add_json(analysis_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = sys.argv[1:] if argv is None else argv
    as_json = "--json" in arguments
    try:
        args = parser.parse_args(arguments)
        return _dispatch(args)
    except WorkbenchError as error:
        payload = {"ok": False, "error": {"code": error.error_code, "message": str(error)}}
        if as_json:
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except OSError as error:
        wrapped = WorkbenchIOError(str(error))
        if as_json:
            print(
                json.dumps(
                    {"ok": False, "error": {"code": wrapped.error_code, "message": str(wrapped)}},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
        else:
            print(f"error: {wrapped}", file=sys.stderr)
        return wrapped.exit_code


def _dispatch(args: argparse.Namespace) -> int:
    if args.group == "notebook" and args.command == "create":
        result = create_notebook(
            args.notebook,
            CreateNotebookRequest(
                kernel_name=args.kernel_name,
                kernel_display_name=args.kernel_display_name,
                language=args.language,
            ),
        )
        _emit_mutation(result.to_dict(), args.json)
        return 0
    if args.group == "cells":
        result = list_cells(args.notebook)
        _emit(result, args.json, _format_cells(result))
        return 0
    if args.group == "cell" and args.command == "get":
        result = get_cell(args.notebook, _selector(args))
        _emit(result, args.json, result["source"])
        return 0
    if args.group == "cell" and args.command == "add":
        result = add_cell(
            args.notebook,
            AddCellRequest(
                cell_type=args.cell_type,
                source=_read_source(args.source_file),
                tags=args.tag,
                before_tag=args.before_tag,
                after_tag=args.after_tag,
                before_cell_id=args.before_cell_id,
                after_cell_id=args.after_cell_id,
                append=args.append,
                expected_sha256=args.expected_sha256,
                require_unexecuted=args.require_unexecuted,
            ),
        )
        _emit_mutation(result.to_dict(), args.json)
        return 0
    if args.group == "cell" and args.command == "replace":
        result = replace_cell(
            args.notebook,
            ReplaceCellRequest(
                selector=_selector(args),
                source=_read_source(args.source_file),
                expected_sha256=args.expected_sha256,
                require_unexecuted=args.require_unexecuted,
            ),
        )
        _emit_mutation(result.to_dict(), args.json)
        return 0
    if args.group == "cell" and args.command == "remove":
        result = remove_cell(
            args.notebook,
            RemoveCellRequest(
                selector=_selector(args),
                expected_sha256=args.expected_sha256,
                require_unexecuted=args.require_unexecuted,
                allow_parameters_cell=args.allow_parameters_cell,
            ),
        )
        _emit_mutation(result.to_dict(), args.json)
        return 0
    if args.group == "tag":
        if args.command == "rename":
            request = TagMutationRequest(
                operation="rename",
                from_tag=args.from_tag,
                to_tag=args.to_tag,
                all_matches=args.all,
                expected_sha256=args.expected_sha256,
                require_unexecuted=args.require_unexecuted,
            )
        else:
            request = TagMutationRequest(
                operation=args.command,
                selector=_selector(args),
                tags=args.tag,
                expected_sha256=args.expected_sha256,
                require_unexecuted=args.require_unexecuted,
            )
        result = update_tags(args.notebook, request)
        _emit_mutation(result.to_dict(), args.json)
        return 0
    if args.group == "script":
        selector = _optional_selector(args)
        print(
            get_script(
                args.notebook,
                selectors=[selector] if selector is not None else None,
                include_markdown=args.include_markdown,
            ),
            end="",
        )
        return 0
    if args.group == "output" and args.command == "get":
        result = get_outputs(args.notebook, _selector(args), args.save_media)
        _emit(result, args.json, render_outputs_text(result))
        return 0
    if args.group == "output" and args.command == "errors":
        result = list_errors(args.notebook)
        text = "".join(
            f"{item['cell_id']}\t{item['index']}\t{','.join(item['tags'])}\t"
            f"{item['ename']}: {item['evalue']}\n"
            for item in result["errors"]
        )
        _emit(result, args.json, text)
        return 0
    if args.group == "validate":
        mode = "source" if args.source else "executed" if args.executed else "common"
        result = validate_notebook(args.notebook, mode)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        elif result.valid:
            print(f"valid ({mode}): {result.path}")
            if mode == "executed":
                print(f"error outputs: {result.error_output_count}")
                print(f"unexecuted code cells: {result.unexecuted_code_cell_count}")
        else:
            for error in result.errors:
                print(f"error: {error}", file=sys.stderr)
        return 0 if result.valid else NotebookValidationError.exit_code
    if args.group == "analysis" and args.command == "init":
        result = initialize_analysis(args.root, args.analysis_id, args.title)
        _emit(result, args.json, result["analysis_dir"])
        return 0
    if args.group == "analysis" and args.command == "add-request":
        result = add_analysis_request(
            args.analysis_dir,
            args.title,
            args.parent_request,
            args.based_on_run,
        )
        _emit(result, args.json, result["request_id"])
        return 0
    if args.group == "analysis" and args.command == "start-run":
        result = start_analysis_run(
            args.analysis_dir,
            args.request_id,
            title=args.title,
            notebook_template=args.notebook_template,
        )
        _emit(result, args.json, result["run_id"])
        return 0
    if args.group == "analysis" and args.command == "execute":
        _validate_execute_runner_options(args)
        result = execute_analysis_run(
            args.analysis_dir,
            args.run_id,
            parameters_file=args.parameters_file,
            kernel=args.kernel,
            cwd=args.cwd,
            start_timeout=args.start_timeout,
            execution_timeout=args.execution_timeout,
            runner=args.runner,
            project_root=args.project_root,
            docker_image=args.docker_image,
            memory=args.memory,
            memory_swap=args.memory_swap,
            mounts=args.mounts,
            environment_variables=args.environment_variables,
        )
        _emit(result, args.json, result["executed_path"])
        return 0
    if args.group == "analysis" and args.command == "set-validation":
        result = set_run_validation(
            args.analysis_dir,
            args.run_id,
            args.check,
            args.result,
        )
        _emit(result, args.json, f"{result['run_id']} {result['check']} {result['result']}")
        return 0
    if args.group == "analysis" and args.command == "recover-run":
        result = recover_analysis_run(args.analysis_dir, args.run_id, args.reason)
        _emit(result, args.json, f"{result['run_id']} {result['status']}")
        return 0
    if args.group == "analysis" and args.command == "complete-run":
        result = complete_analysis_run(args.analysis_dir, args.run_id)
        _emit(result, args.json, f"{result['run_id']} {result['status']}")
        return 0
    if args.group == "analysis" and args.command == "accept-run":
        result = accept_analysis_run(args.analysis_dir, args.run_id)
        _emit(result, args.json, f"{result['run_id']} accepted")
        return 0
    if args.group == "analysis" and args.command == "set-status":
        result = set_analysis_status(args.analysis_dir, args.status)
        _emit(result, args.json, f"{result['analysis_id']} {result['status']}")
        return 0
    if args.group == "analysis" and args.command == "validate":
        result = validate_analysis_workspace(args.analysis_dir, portable=args.portable)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        elif result.valid:
            mode = "portable" if args.portable else "strict"
            print(f"valid ({mode}): {result.path}")
        else:
            for error in result.errors:
                print(f"error: {error}", file=sys.stderr)
        return 0 if result.valid else AnalysisValidationError.exit_code
    raise RuntimeError(f"unhandled command: {args.group} {getattr(args, 'command', '')}")


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def _validate_execute_runner_options(args: argparse.Namespace) -> None:
    if args.runner == "docker":
        if args.memory is None:
            raise CLIUsageError("--memory is required with --runner docker")
        return
    docker_options = {
        "--project-root": args.project_root,
        "--docker-image": args.docker_image,
        "--memory": args.memory,
        "--memory-swap": args.memory_swap,
        "--mount": args.mounts,
        "--env": args.environment_variables,
    }
    supplied = [name for name, value in docker_options.items() if value]
    if supplied:
        raise CLIUsageError(
            "Docker options require --runner docker: " + ", ".join(supplied)
        )


def _add_selector(
    parser: argparse.ArgumentParser,
    *,
    allow_index: bool = False,
    required: bool = True,
    tag_option: str = "--tag",
) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--cell-id")
    group.add_argument(tag_option, dest="selector_tag")
    if allow_index:
        group.add_argument("--index", type=int)


def _add_source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-file",
        type=Path,
        help="read source from this UTF-8 file; omit to read stdin",
    )


def _add_mutation_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-sha256")
    parser.add_argument("--require-unexecuted", action="store_true")
    _add_json(parser)


def _selector(args: argparse.Namespace) -> CellSelector:
    return CellSelector(
        cell_id=getattr(args, "cell_id", None),
        tag=getattr(args, "selector_tag", None),
        index=getattr(args, "index", None),
    )


def _optional_selector(args: argparse.Namespace) -> CellSelector | None:
    if not any(
        getattr(args, name, None) is not None for name in ("cell_id", "selector_tag", "index")
    ):
        return None
    return _selector(args)


def _read_source(source_file: Path | None) -> str:
    if source_file is None:
        return sys.stdin.read()
    try:
        return source_file.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkbenchIOError(f"could not read source file {source_file}: {error}") from error


def _emit(result: dict[str, Any], as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(text, end="" if text.endswith("\n") or not text else "\n")


def _emit_mutation(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"SHA256 {result['sha256']}")
        for cell_id in result["cell_ids"]:
            print(f"CELL_ID {cell_id}")


def _format_cells(result: dict[str, Any]) -> str:
    lines = [f"NOTEBOOK_SHA256 {result['sha256']}", "ID\tINDEX\tTYPE\tTAGS\tEXECUTION\tOUTPUTS\tSOURCE"]
    for cell in result["cells"]:
        lines.append(
            "\t".join(
                [
                    cell["id"],
                    str(cell["index"]),
                    cell["cell_type"],
                    ",".join(cell["tags"]),
                    "" if cell["execution_count"] is None else str(cell["execution_count"]),
                    str(cell["output_count"]),
                    cell["source_first_line"].replace("\t", "    "),
                ]
            )
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
