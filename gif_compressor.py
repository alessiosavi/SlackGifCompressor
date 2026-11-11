"""Compatibility shim for the original ``gif_compressor.py`` entry point."""

from slackgif.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
