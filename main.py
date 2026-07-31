"""Command-line entrypoint for the chemical market crawler prototype."""

from __future__ import annotations

import argparse
import json
import sys

from aggregator import check_market_availability
from crawlers.clearsynth import search_clearsynth
from crawlers.anantlabs import search_anantlabs
from crawlers.simsonpharma import search_simsonpharma
from crawlers.synzeal import search_synzeal


def main() -> int:
    parser = argparse.ArgumentParser(description="Search a supported connector for a CAS number.")
    parser.add_argument(
        "source",
        choices=("synzeal", "anantlabs", "simsonpharma", "clearsynth", "pharmaffiliates", "all"),
        help="Connector to test",
    )
    parser.add_argument("cas_number", help="CAS number to search")
    args = parser.parse_args()

    if args.source == "synzeal":
        result = search_synzeal(args.cas_number)
    elif args.source == "anantlabs":
        result = search_anantlabs(args.cas_number)
    elif args.source == "simsonpharma":
        result = search_simsonpharma(args.cas_number)
    elif args.source == "clearsynth":
        result = search_clearsynth(args.cas_number)
    else:
        result = check_market_availability(args.cas_number)

    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
