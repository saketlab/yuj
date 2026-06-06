#!/usr/bin/env python3
"""Fetch one page and write its word count.

yuj calls this once per line of ``urls.txt`` with the line as the last argument,
and skips any item whose output already exists (resume-safe). Each item is a bare
domain (e.g. ``example.com``); we fetch ``https://<item>`` and write
``$YUJ_OUT/<item>.count``, the same path yuj's resume check looks for.
"""

import os
import re
import sys
import urllib.request
from pathlib import Path


def main() -> None:
    item = sys.argv[1]
    out_dir = Path(os.environ.get("YUJ_OUT", "results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{item}.count"
    try:
        with urllib.request.urlopen(f"https://{item}", timeout=30) as resp:
            text = resp.read().decode("utf-8", "replace")
        out.write_text(f"{item}\t{len(re.findall(r'\\w+', text))}\n")
    except Exception as exc:  # record failure, don't crash the batch
        out.write_text(f"{item}\tERROR\t{exc}\n")


if __name__ == "__main__":
    main()
