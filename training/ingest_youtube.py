"""Stage 1a — INGEST (YouTube): download source videos via yt-dlp.

Downloads each URL at up to 720p into ``--out`` and records source URL,
title and license per video in ``manifest.json``. YouTube material is for
**prototyping only** — see ``training/README.md`` (licensing policy).

Usage:
    python training/ingest_youtube.py --urls-file urls.txt --out data/raw/
    python training/ingest_youtube.py --url https://youtu.be/... --out data/raw/

Exit codes: 0 = all downloads OK, 2 = at least one download failed
(network/proxy/invalid URL) — stderr explains what to do.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # standalone: make `training._common` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training._common import UNKNOWN_LICENSE, load_manifest, write_manifest  # noqa: E402

FAILURE_GUIDANCE = """\
ingest_youtube: one or more downloads FAILED.

What to check:
  * Is the URL valid and the video public (not private/geo-blocked/removed)?
  * Does this machine have outbound network access? Behind a proxy, make sure
    HTTPS_PROXY / HTTP_PROXY are exported in this shell.
  * Is yt-dlp up to date? YouTube changes frequently:
        .venv/bin/pip install -U yt-dlp
  * No network at all? Use local footage through the same pipeline instead:
        python training/ingest_local.py --videos my_clip.mp4 --out data/raw/
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download YouTube videos (<=720p) into a raw-data directory + manifest.",
        epilog="YouTube data is for prototyping only; see training/README.md (licensing).",
    )
    parser.add_argument("--url", action="append", default=[], help="video URL (repeatable)")
    parser.add_argument("--urls-file", help="text file with one URL per line (# = comment)")
    parser.add_argument("--out", required=True, help="output directory, e.g. data/raw/")
    parser.add_argument(
        "--max-height", type=int, default=720, help="maximum video height (default: 720)"
    )
    return parser


def collect_urls(args: argparse.Namespace) -> list[str]:
    urls: list[str] = []
    if args.urls_file:
        for raw in Path(args.urls_file).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    urls.extend(args.url)
    return urls


def _download_one(url: str, out_dir: Path, max_height: int) -> dict[str, Any]:
    """Download one video; returns its manifest entry. Raises on failure."""
    import yt_dlp

    opts = {
        # Single pre-merged file (no ffmpeg merge needed), capped at max_height.
        "format": f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 1,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        assert info is not None
        filename = Path(ydl.prepare_filename(info)).name
    return {
        "file": filename,
        "source_url": info.get("webpage_url") or url,
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration_s": info.get("duration"),
        "license": info.get("license") or UNKNOWN_LICENSE,
    }


def _merge_videos(out_dir: Path, new_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge new entries into any existing ingest manifest (keyed by file name)."""
    existing = load_manifest(out_dir) or {}
    videos = {v["file"]: v for v in existing.get("videos", []) if isinstance(v, dict)}
    for entry in new_entries:
        videos[entry["file"]] = entry
    return [videos[key] for key in sorted(videos)]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    urls = collect_urls(args)
    if not urls:
        parser.error("no URLs given — use --url and/or --urls-file")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []
    for url in urls:
        try:
            entry = _download_one(url, out_dir, args.max_height)
        except Exception as exc:  # yt-dlp raises many error types (network, extractor, ...)
            failures.append((url, f"{type(exc).__name__}: {exc}"))
            continue
        entries.append(entry)
        print(f"downloaded: {entry['file']}  ({entry['title']!r}, license: {entry['license']})")

    if entries:
        write_manifest(
            out_dir,
            "ingest",
            {"max_height": args.max_height},
            videos=_merge_videos(out_dir, entries),
            licensing_note="YouTube sources are for prototyping only (training/README.md).",
        )

    if failures:
        for url, message in failures:
            print(f"FAILED: {url}\n    {message}", file=sys.stderr)
        print(f"\n{FAILURE_GUIDANCE}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
