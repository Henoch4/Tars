#!/usr/bin/env python3
"""CLI wrapper for src.meme_exit — stop-loss, profit ratchet, live sell."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.meme_exit import main

if __name__ == "__main__":
    sys.exit(main())
