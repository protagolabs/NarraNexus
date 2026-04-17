"""Smoke test for CommonToolsModule.web_search — verifies DDG path returns hits.

Run:  uv run python scripts/test_common_tools_web_search.py
"""

import asyncio
import sys

from xyz_agent_context.module.common_tools_module._common_tools_impl.web_search import (
    search_many,
    format_results,
)


async def main() -> int:
    queries = [
        "claude agent sdk",
        "python asyncio gather exception propagation",
    ]
    print(f"Running {len(queries)} queries in parallel...")
    bundles = await search_many(queries, max_results_per_query=3)

    total = sum(len(b["results"]) for b in bundles)
    errors = [b for b in bundles if b["error"]]
    print(f"  total hits = {total}, errored queries = {len(errors)}\n")

    print(format_results(bundles))

    if total == 0:
        print("\nFAIL: got 0 hits — DDG may be rate-limiting, check network")
        return 1
    print("\nOK: web_search returned hits")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
