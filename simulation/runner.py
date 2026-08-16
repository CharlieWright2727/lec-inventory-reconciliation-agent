"""CLI entry point for the live warehouse reconciliation simulation."""

import argparse
import asyncio
import random
from pathlib import Path

from simulation.controller import run_simulation
from simulation.models import SimulationStatus
from simulation.reporter import SimulationReporter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all five live warehouse disturbances through V3."
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Deterministic random seed; generated automatically when omitted.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optional path for the machine-readable simulation report.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**32)
    result = await run_simulation(seed, reporter=SimulationReporter())
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print()
        print(f"JSON report: {args.json_report}")
    return 0 if result.overall_result == SimulationStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
