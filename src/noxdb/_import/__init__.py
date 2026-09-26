"""Master import subpackage: project-folder → database in one transaction.

Public surface:

- :func:`runner.import_project_from_dir` validates and imports a folder.
  The :mod:`scripts.import_project` CLI is a thin wrapper around it.
- :func:`runner.validate_bundle` runs the same checks without writing,
  on a bundle read by :func:`loader.load_project_dir` or built in memory
  from the row dataclasses.
"""

from noxdb._import.loader import (
    FileRow,
    ProjectBundle,
    ProjectMeta,
    SampleRow,
    SubjectRow,
    VisitRow,
    load_project_dir,
)
from noxdb._import.runner import (
    ProjectImportError,
    ValidationResult,
    import_project_from_dir,
    validate_bundle,
)

__all__ = [
    "FileRow",
    "ProjectBundle",
    "ProjectImportError",
    "ProjectMeta",
    "SampleRow",
    "SubjectRow",
    "ValidationResult",
    "VisitRow",
    "import_project_from_dir",
    "load_project_dir",
    "validate_bundle",
]
