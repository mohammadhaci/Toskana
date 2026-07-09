"""Allow `python -m toskana ...`."""

import sys

from toskana.cli import main

if __name__ == "__main__":
    sys.exit(main())
