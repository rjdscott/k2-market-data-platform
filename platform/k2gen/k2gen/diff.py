"""`k2gen diff`, filled by step S08: what changes between two releases.

CI posts the output as the validation runner's job summary, against the newest `v3.*` tag.
"""

from pathlib import Path


def diff(old: Path, new: Path) -> str:
    """The file-level and storage-plan difference of two release directories, as Markdown."""
    raise NotImplementedError("S08")
