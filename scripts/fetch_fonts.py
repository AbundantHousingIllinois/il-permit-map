#!/usr/bin/env python3
"""Fetch the Poppins font files the link-preview cards are drawn with.

  github.com/google/fonts/raw/main/ofl/poppins/Poppins-*.ttf
      -- AHIL's text face, SIL Open Font License, from Google Fonts' own
         repository. Three weights: Regular, SemiBold, Bold.

The page itself never requests a font (CLAUDE.md, *No webfont*). These files
are used only by scripts/previews.py, which runs inside the offline build and
so cannot fetch anything itself; they are cached under data/raw/vendor/fonts/.

Idempotent: already-downloaded files are left alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L


def main() -> int:
    print("=" * 78)
    print("Fetching Poppins for the link-preview cards")
    print("=" * 78)
    for name in L.FONTS.values():
        L.download(L.FONT_BASE_URL + name, L.FONT_DIR / name,
                   note="Poppins, SIL Open Font License; link-preview cards only")
    # The OFL asks that the licence travel with the font files.
    L.download(L.FONT_BASE_URL + "OFL.txt", L.FONT_DIR / "OFL.txt",
               note="SIL Open Font License for the Poppins files beside it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
