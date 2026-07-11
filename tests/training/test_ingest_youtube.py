"""ingest_youtube failure path: invalid/blocked URL -> exit 2 + guidance.

No network is assumed: an unparseable URL makes yt-dlp fail before any
request, and a blocked/unreachable URL fails through the same path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from training import ingest_youtube


def test_invalid_url_exits_2_with_guidance(tmp_path: Path, capsys) -> None:
    rc = ingest_youtube.main(["--url", "definitely-not-a-video-url", "--out", str(tmp_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "definitely-not-a-video-url" in err
    # actionable guidance: proxy hint, yt-dlp update hint, offline alternative
    assert "HTTPS_PROXY" in err
    assert "pip install -U yt-dlp" in err
    assert "ingest_local.py" in err
    # nothing downloaded -> no manifest written
    assert not (tmp_path / "manifest.json").exists()


def test_urls_file_with_comments(tmp_path: Path, capsys) -> None:
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("# restaurant pass-through clips\n\nnot-a-url-either\n")
    rc = ingest_youtube.main(["--urls-file", str(urls_file), "--out", str(tmp_path / "raw")])
    assert rc == 2
    assert "not-a-url-either" in capsys.readouterr().err


def test_no_urls_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        ingest_youtube.main(["--out", str(tmp_path)])
    assert excinfo.value.code == 2  # argparse usage error
