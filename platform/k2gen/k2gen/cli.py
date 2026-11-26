"""k2gen: the v2 spine's command line (platform/k2gen/INTERFACES.md).

    uv run --project platform/k2gen k2gen docset --tag <tag> --out <dir>
    uv run --project platform/k2gen k2gen check --set <dir> --previous <dir|none>
    uv run --project platform/k2gen k2gen release --set <dir> --previous <dir|none> --images <json> --out <root>
    uv run --project platform/k2gen k2gen diff <old release> <new release>

The old format's validate, generate and new, and the files generate wrote into the repository,
were deleted in step S23: a release compiles everything it runs (k2gen.release).
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _optional_dir(value: str) -> Path | None:
    return None if value == "none" else Path(value)


def parser() -> argparse.ArgumentParser:
    """Every subcommand: docset, check, release and diff (platform/k2gen/INTERFACES.md)."""
    parser = argparse.ArgumentParser(prog="k2gen", description="Data product contracts for the K2 lake (ADR-032).")
    sub = parser.add_subparsers(dest="command", required=True)
    docset = sub.add_parser("docset", help="extract a release tag's document set and its provenance.json (A2)")
    docset.add_argument("--tag", required=True, help="an annotated release tag")
    docset.add_argument("--out", required=True, type=Path, help="an empty directory to write the set to")
    docset.add_argument("--repo", type=Path, default=ROOT, help="the git repository (default: this checkout)")
    check = sub.add_parser("check", help="run the checks library on a document set; exit 1 on any error (A3)")
    check.add_argument("--set", required=True, type=Path, dest="set_dir", help="a document set directory")
    check.add_argument("--previous", required=True, type=_optional_dir, help="the previous release's document set, or none")
    release = sub.add_parser("release", help="compile a document set into a sealed release; print its id (A1, A3)")
    release.add_argument("--set", required=True, type=Path, dest="set_dir", help="a document set directory")
    release.add_argument("--previous", required=True, type=_optional_dir, help="the previous release directory, or none")
    release.add_argument("--images", required=True, type=Path, help="a JSON file of compose service to image ref")
    release.add_argument("--out", required=True, type=Path, help="the releases root")
    release.add_argument("--compose", type=Path, help="the compose file the release runs (default: the set's own docker-compose.yml)")
    diff = sub.add_parser("diff", help="print the file-level and storage-plan difference of two releases, as Markdown")
    diff.add_argument("old", type=Path)
    diff.add_argument("new", type=Path)
    return parser


def _spine(args: argparse.Namespace) -> int:
    from k2gen import checks, diff, docset, release

    if args.command == "docset":
        docset.write(docset.load(args.tag, args.repo), args.out)
        return 0
    if args.command == "check":
        previous = docset.read(args.previous) if args.previous else None
        findings = checks.run(docset.read(args.set_dir), previous)
        for f in findings:
            where = f"{f.path} {f.pointer}" if f.pointer else f.path
            print(f"{f.severity} {f.code} {where}: {f.message}", file=sys.stderr)
        return 1 if any(f.severity == "error" for f in findings) else 0
    if args.command == "release":
        compose = args.compose or args.set_dir / "docker-compose.yml"  # k2gen.model.COMPOSE
        print(release.release(args.set_dir, args.previous, args.images, args.out, compose))
        return 0
    print(diff.diff(args.old, args.new))
    return 0


def main(argv: list[str] | None = None) -> int:
    return _spine(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
