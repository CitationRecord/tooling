"""Command line interface for the bulk data loader.

    py -m bulk list                    what the generation holds, with sizes
    py -m bulk fetch --only schema     download, resumably
    py -m bulk verify                  recheck files against the manifest

Nothing downloads without being asked for. `list` never writes to disk.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

from . import BUCKET_URL, DEFAULT_GENERATION, USER_AGENT, __version__
from .catalog import BY_KEY, CATALOG, EXCLUDED, plan
from .config import Config, UnsafeDestination, default_directory
from .download import (
    IntegrityError,
    Manifest,
    FileRecord,
    download,
    free_space,
    iso_utc,
    load_manifest,
    save_manifest,
    sha256_file,
    verify,
)

GIB = 1024 ** 3


def _out(message: str = "") -> None:
    print(message, flush=True)


def _size(nbytes: int) -> str:
    if nbytes >= GIB:
        return f"{nbytes / GIB:.2f} GiB"
    if nbytes >= 1024 ** 2:
        return f"{nbytes / 1024 ** 2:.1f} MiB"
    return f"{nbytes / 1024:.0f} KiB"


def _config(args) -> Config:
    directory = Path(args.dir) if args.dir else default_directory(args.generation)
    return Config(
        generation=args.generation,
        directory=directory,
        keys=tuple(args.only or ()),
    ).resolved()


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _provenance(config: Config) -> dict:
    from resolve.journal import run_provenance

    base = run_provenance()
    base.update({
        "bulk_tool_version": __version__,
        "source": BUCKET_URL,
        "generation": config.generation,
        "authenticated": False,
        "note": "Public bucket; no credentials used.",
    })
    base.pop("api", None)
    return base


def cmd_list(args) -> int:
    """Show what the generation holds. Writes nothing."""
    config = _config(args)
    entries, missing, available = plan(config.generation, config.keys, _session())

    _out(f"generation {config.generation}   objects in bucket: {len(available)}")
    _out(f"target     {config.directory}   (nothing written by this command)")
    _out()
    _out(f"{'key':<17} {'size':>10}  object")
    _out("-" * 78)
    total = 0
    for bulk_file, listing in entries:
        total += listing["size"]
        _out(f"{bulk_file.key:<17} {_size(listing['size']):>10}  "
             f"{listing['key'].split('/')[-1]}")
    _out("-" * 78)
    _out(f"{'total':<17} {_size(total):>10}  {len(entries)} file(s), {total:,} bytes")

    if missing:
        _out()
        for key in missing:
            _out(f"MISSING from this generation: {key}")

    _out()
    _out("excluded on purpose:")
    for key, why in EXCLUDED.items():
        listed = next(
            (v for k, v in available.items() if k.split("/")[-1].startswith(f"{key}-")),
            None,
        )
        size = f" ({_size(listed['size'])})" if listed else ""
        _out(f"  {key}{size}: {why}")

    free = free_space(config.directory)
    _out()
    _out(f"disk: {_size(total)} to download, {_size(free)} free at the target")
    if free < total * 2:
        _out("WARNING: less than twice the download size is free")
    return 0


def cmd_fetch(args) -> int:
    config = _config(args)
    session = _session()
    entries, missing, _ = plan(config.generation, config.keys, session)

    if missing:
        for key in missing:
            _out(f"error: not in generation {config.generation}: {key}")
        return 1
    if not entries:
        _out("error: nothing selected")
        return 1

    wanted = sum(listing["size"] for _, listing in entries)
    free = free_space(config.directory)
    _out(f"generation {config.generation}")
    _out(f"directory  {config.directory}")
    _out(f"download   {_size(wanted)} across {len(entries)} file(s)")
    _out(f"disk       {_size(free)} free")
    _out()

    if free < wanted * 2 and not args.yes:
        _out("refusing: less than twice the download size is free. Pass --yes to override.")
        return 1

    manifest = load_manifest(config.manifest_path) or Manifest(
        generation=config.generation, directory=str(config.directory),
        excluded=dict(EXCLUDED),
    )
    manifest.provenance = _provenance(config)

    for bulk_file, listing in entries:
        destination = config.directory / listing["key"].split("/")[-1]
        existing = manifest.files.get(bulk_file.key)
        if existing and not args.force and not verify(destination, existing):
            _out(f"{bulk_file.key:<17} already present and verified, skipping")
            continue

        url = BUCKET_URL + listing["key"]
        _out(f"{bulk_file.key:<17} {_size(listing['size']):>10}  fetching ...")
        started = time.monotonic()
        try:
            written, resumed = download(
                url, destination, listing["size"], etag=listing.get("etag"),
                session=session, timeout=config.timeout,
            )
        except (IntegrityError, requests.RequestException) as exc:
            _out(f"{'':<17} FAILED {exc}")
            save_manifest(manifest, config.manifest_path)
            return 1

        digest = sha256_file(destination)
        manifest.record(FileRecord(
            key=bulk_file.key,
            object_key=listing["key"],
            path=destination.name,
            bytes=written,
            sha256=digest,
            etag=listing.get("etag"),
            etag_verified=bool(
                listing.get("etag") and "-" not in listing["etag"]
                and len(listing["etag"]) == 32
            ),
            last_modified=listing.get("last_modified"),
            downloaded_at_utc=iso_utc(),
            resumed=resumed,
            purpose=bulk_file.purpose,
        ))
        save_manifest(manifest, config.manifest_path)
        elapsed = time.monotonic() - started
        _out(f"{'':<17} done in {elapsed:.1f}s"
             f"{' (resumed)' if resumed else ''}  sha256 {digest[:16]}")

    _out()
    _out(f"manifest   {config.manifest_path}")
    _out(f"files      {len(manifest.files)}, {_size(manifest.total_bytes)}")
    return 0


def cmd_verify(args) -> int:
    config = _config(args)
    manifest = load_manifest(config.manifest_path)
    if manifest is None:
        _out(f"no manifest at {config.manifest_path}")
        return 1

    problems = []
    for key, record in sorted(manifest.files.items()):
        found = verify(config.directory / record["path"], record)
        status = "ok" if not found else "FAILED"
        _out(f"{key:<17} {status:<7} {record['path']}")
        for problem in found:
            _out(f"{'':<17}         {problem}")
        problems.extend(found)

    _out()
    _out(f"generation {manifest.generation}, {len(manifest.files)} file(s), "
         f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_rows(args) -> int:
    """Count data rows and record them in the manifest, as provenance."""
    config = _config(args)
    manifest = load_manifest(config.manifest_path)
    if manifest is None:
        _out(f"no manifest at {config.manifest_path}")
        return 1

    from .read import count_rows

    wanted = set(config.keys) if config.keys else set(manifest.files)
    for key in sorted(wanted):
        record = manifest.files.get(key)
        if record is None:
            _out(f"{key:<17} not downloaded")
            continue
        path = config.directory / record["path"]
        if not path.name.endswith(".bz2"):
            _out(f"{key:<17} skipped (not a CSV file)")
            continue
        if record.get("rows") is not None and not args.force:
            _out(f"{key:<17} {record['rows']:>12,} rows (already counted)")
            continue

        _out(f"{key:<17} counting ...")
        started = time.monotonic()
        header, rows = count_rows(
            path,
            on_progress=lambda n: _out(f"{'':<17} {n:>12,} rows so far"),
        )
        record["rows"] = rows
        record["columns"] = header
        record["counted_at_utc"] = iso_utc()
        manifest.files[key] = record
        save_manifest(manifest, config.manifest_path)
        _out(f"{'':<17} {rows:>12,} rows, {len(header)} columns, "
             f"{time.monotonic() - started:.1f}s")
        _out(f"{'':<17} columns: {', '.join(header)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bulk",
        description="Download CourtListener bulk data for exact coverage measurement.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"bulk {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def common(sub):
        sub.add_argument("--generation", default=DEFAULT_GENERATION, metavar="YYYY-MM-DD")
        sub.add_argument("--dir", metavar="PATH",
                         help=f"destination (default: {default_directory()})")
        sub.add_argument("--only", action="append", metavar="KEY",
                         choices=sorted(BY_KEY), help="repeatable; "
                         f"one of {', '.join(sorted(BY_KEY))}")

    list_parser = subparsers.add_parser("list", help="what the generation holds")
    common(list_parser)
    list_parser.set_defaults(func=cmd_list)

    fetch_parser = subparsers.add_parser("fetch", help="download, resumably")
    common(fetch_parser)
    fetch_parser.add_argument("--force", action="store_true",
                              help="re-download files already verified")
    fetch_parser.add_argument("--yes", action="store_true",
                              help="proceed despite a low disk space warning")
    fetch_parser.set_defaults(func=cmd_fetch)

    rows_parser = subparsers.add_parser("rows", help="count rows into the manifest")
    common(rows_parser)
    rows_parser.add_argument("--force", action="store_true", help="recount")
    rows_parser.set_defaults(func=cmd_rows)

    verify_parser = subparsers.add_parser("verify", help="recheck against the manifest")
    common(verify_parser)
    verify_parser.set_defaults(func=cmd_verify)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except UnsafeDestination as exc:
        _out(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        _out("\ninterrupted; partial downloads resume on the next run")
        return 130


if __name__ == "__main__":
    sys.exit(main())
