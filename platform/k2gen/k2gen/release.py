"""The compiler, `k2gen release` (A1, A3), filled by step S08.

Pure: its only inputs are a document set, the previous release, the image refs and the set's
compose file. It builds <out_root>/.<id>.tmp/ with documents/ (the set, as docset.write writes
it), provenance.json, model.RELEASE_COMPOSE (the compose file, byte for byte), every emitter's
files through k2gen.emit.run (a stub is skipped with a line on stderr until S23; two emitters
writing one path is an error) and manifest.json last, whose `files` hash every other file and
whose `needs` follow model.need_of against `previous`; then `chmod -R a-w` and an atomic rename
to <out_root>/<id>/. Every emitted file with a schema (model.SCHEMAS) is validated against it.
"""

from pathlib import Path

from k2gen.model import Release


def release(set_dir: Path, previous: Path | None, images: Path, out_root: Path, compose: Path) -> str:
    """Compile the set at `set_dir` against the release directory `previous` (None for the
    first), with `images` a JSON file of service to image ref (model.SCHEMAS["images"]) and
    `compose` the compose file the release runs (the CLI's default: the set's own
    model.COMPOSE); return the new release id (model.release_id). Compiling one set twice
    gives one id."""
    raise NotImplementedError("S08")


def read(release_dir: Path) -> Release:
    """Open a sealed release directory, validating its manifest."""
    raise NotImplementedError("S08")
