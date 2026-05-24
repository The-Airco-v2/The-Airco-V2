#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))

from airco.deploy_env import render_env


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    template_path = Path(args.template)
    output_path = Path(args.output)
    rendered = render_env(template_path.read_text(), os.environ)
    output_path.write_text(rendered)


if __name__ == "__main__":
    main()
