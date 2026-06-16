"""Make ``src/`` and ``tests/`` importable when running under pytest without
an editable install. (The same path insertion is also done in
``tests/fixture_db.py`` so the suite runs under plain ``unittest`` too.)
"""

import os
import sys

_ROOT = os.path.dirname(__file__)
for sub in ("src", "tests"):
    path = os.path.join(_ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)
