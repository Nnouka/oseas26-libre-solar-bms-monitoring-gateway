#!/usr/bin/env python3
"""Workaround for networks that block composer.github.io.

MPM's Dockerfiles download the Composer installer and verify it against a checksum
from composer.github.io. If that host is blocked, the build fails. This replaces that
block with a copy of Composer from the official Docker image (same version).

Usage: python fix_composer_dockerfiles.py <path-to-mpm>/docker
Local change in your MPM clone only; do not commit it to MPM.
"""
import re
import sys
from pathlib import Path

PATTERN = re.compile(
    r'RUN EXPECTED_CHECKSUM=.*?RUN mv composer\.phar /usr/local/bin/composer\n', re.S)
REPLACEMENT = 'COPY --from=composer:2.8.8 /usr/bin/composer /usr/local/bin/composer\n'

for f in sorted(Path(sys.argv[1]).glob("Dockerfile*")):
    text = f.read_text(encoding="utf-8")
    new, n = PATTERN.subn(REPLACEMENT, text)
    if n:
        f.write_text(new, encoding="utf-8", newline="\n")
        print("patched", f.name)
    else:
        print("skipped", f.name)
