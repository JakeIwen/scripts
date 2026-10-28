"""Build binary iOS workflows and sign installable copies with macOS Shortcuts."""
import argparse
from pathlib import Path
import plistlib
import subprocess
import sys

from .mac_workflows import create_workflow as create_mac_workflow
from .pi_shortcuts import DEFAULT_OUTPUT_DIRECTORY
from .workflows import create_workflows as create_pi_workflows


def create_workflows():
    """Keep the Pi helper before its callers, then append the Mac share-sheet workflow."""
    return create_pi_workflows() + [create_mac_workflow()]


def build_shortcuts(output_directory, workflows=None, *, sign=True):
    """Write all workflows; unsigned builds are explicitly named as such."""
    directory = Path(output_directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for workflow in create_pi_workflows() if workflows is None else workflows:
        name = workflow["WFWorkflowName"]
        unsigned = directory / (name + ".unsigned.shortcut")
        with unsigned.open("wb") as stream:
            plistlib.dump(workflow, stream, fmt=plistlib.FMT_BINARY, sort_keys=False)
        destination = directory / (name + ".shortcut")
        if sign:
            # A failed rebuild must not leave an older file looking newly signed.
            destination.unlink(missing_ok=True)
            subprocess.run(
                ["/usr/bin/shortcuts", "sign", "--mode", "anyone", "--input", str(unsigned),
                 "--output", str(destination)],
                check=True, capture_output=True, text=True,
            )
        paths.append(destination if sign else unsigned)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY,
        help="build directory (default: macbook/build/shortcuts in this checkout)",
    )
    parser.add_argument(
        "--no-sign", action="store_true",
        help="write binary .unsigned.shortcut plists only; these cannot be imported",
    )
    options = parser.parse_args(argv)
    try:
        paths = build_shortcuts(options.output_dir, create_workflows(), sign=not options.no_sign)
    except subprocess.CalledProcessError as error:
        print("Shortcut signing failed: " + (error.stderr or str(error)).strip(), file=sys.stderr)
        return 1
    except OSError as error:
        print("Shortcut build failed: " + str(error), file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
