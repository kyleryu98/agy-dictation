import argparse
import sys
from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description="AGY CLI-backed desktop dictation (macOS preview)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("Desktop integration is currently implemented for macOS only.")
    from .macos.service import main as run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
