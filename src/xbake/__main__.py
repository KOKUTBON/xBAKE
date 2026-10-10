"""
Entry point when the package is invoked with `python -m xbake`.
"""

import sys

from xbake.cli import main

if __name__ == "__main__":
    sys.exit(main())
