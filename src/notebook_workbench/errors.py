"""Domain errors and their stable CLI exit codes."""


class WorkbenchError(Exception):
    exit_code = 1
    error_code = "workbench_error"


class CLIUsageError(WorkbenchError):
    exit_code = 2
    error_code = "cli_argument_error"


class SelectionError(WorkbenchError):
    exit_code = 3
    error_code = "selection_error"


class CellNotFoundError(SelectionError):
    error_code = "cell_not_found"


class AmbiguousCellError(SelectionError):
    error_code = "ambiguous_cell"


class ConflictError(WorkbenchError):
    exit_code = 4
    error_code = "sha256_conflict"


class NotebookValidationError(WorkbenchError):
    exit_code = 5
    error_code = "notebook_invalid"


class WorkbenchIOError(WorkbenchError):
    exit_code = 6
    error_code = "io_error"
