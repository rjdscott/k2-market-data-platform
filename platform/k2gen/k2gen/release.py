"""The compiler, `k2gen release` (A1, A3), filled by step S08.

Pure: its only inputs are a document set, the previous release and the image refs. It
builds <out_root>/.<id>.tmp/ with documents/ (the set, as docset.write writes it),
provenance.json, every k2gen.emit module's files (two emitters writing one path is an
error) and manifest.json last; then `chmod -R a-w` and an atomic rename to <out_root>/<id>/.
"""

from pathlib import Path

from k2gen.model import Release


def release(set_dir: Path, previous: Path | None, images: Path, out_root: Path) -> str:
    """Compile the set at `set_dir` against the release directory `previous` (None for the
    first), with `images` a JSON file of service to image ref (model.SCHEMAS["images"]);
    return the new release id (model.release_id). Compiling one set twice gives one id."""
    raise NotImplementedError("S08")


def read(release_dir: Path) -> Release:
    """Open a sealed release directory, validating its manifest."""
    raise NotImplementedError("S08")
