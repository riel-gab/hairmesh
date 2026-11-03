"""The licence header of every .py of the add-on: applies it and judges it.

    python tools/license_headers.py          # the judge: lists the files without it, exit 1 if any
    python tools/license_headers.py --apply  # writes the missing lines (idempotent), then judges

Every .py under hairmesh/ starts with SPDX_LINE and COPYRIGHT_LINE; the modules of hairmesh/core/ that port the
original (a "port of" comment) carry PORT_LINE as well. fdlibm.py ports Sun's library, not the original: its notice
is FDLIBM_LINES, which that library's licence asks to preserve.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, 'hairmesh')
SPDX_LINE = '# SPDX-License-Identifier: GPL-3.0-or-later'
COPYRIGHT_LINE = '# Copyright (C) 2026 Riel'
COPYRIGHT_PREFIX = '# Copyright (C) 2026 '                  # (a line with an older owner is replaced, not kept)
PORT_LINE = "# Ported from Stylized HairMesh Creator 1.0.1 by catarackta (https://catarackta.netlify.app/), used with the author's permission."
FDLIBM_LINES = [
    '# Portions: fdlibm 5.3, Copyright (C) 1993 by Sun Microsystems, Inc. All rights reserved. Developed at SunSoft, a',
    '# Sun Microsystems, Inc. business. Permission to use, copy, modify, and distribute this software is freely granted,',
    '# provided that this notice is preserved.',
]
PORTED = re.compile(r'\bport of\b|\bports of\b', re.I)


def py_files(root=ROOT):
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for n in sorted(names):
            if n.endswith('.py'):
                yield os.path.join(base, n)


def wanted(path, text):
    """the header lines this file must start with"""
    lines = [SPDX_LINE, COPYRIGHT_LINE]
    rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
    if rel == 'core/fdlibm.py':
        return lines + FDLIBM_LINES
    if rel.startswith('core/') and PORTED.search(text):
        lines.append(PORT_LINE)
    return lines


def problem(path):
    """what is wrong with the file's header, or None"""
    with open(path, encoding='utf-8', newline='') as f:
        text = f.read()
    want = wanted(path, text)
    if text.split('\n')[:len(want)] != want:
        return 'missing or wrong header'
    return None


def apply(path):
    with open(path, encoding='utf-8', newline='') as f:
        text = f.read()
    want = wanted(path, text)
    body = text.split('\n')
    while body and body[0].startswith('# ') and (body[0] in (SPDX_LINE, PORT_LINE) or body[0].startswith(COPYRIGHT_PREFIX)
                                                 or body[0] in FDLIBM_LINES):
        body.pop(0)
    new = '\n'.join(want + body)
    if new != text:
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(new)
        return True
    return False


def main(argv):
    if '--apply' in argv:
        n = sum(apply(p) for p in py_files())
        print(f'headers written: {n}')
    bad = [(p, problem(p)) for p in py_files()]
    bad = [(os.path.relpath(p, REPO), w) for p, w in bad if w]
    for p, w in bad:
        print(f'{p}: {w}')
    print(f'{len(list(py_files()))} files, {len(bad)} without the header')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
