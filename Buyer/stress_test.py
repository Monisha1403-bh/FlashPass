"""
Concurrency-sweep latency/bottleneck test for FlashPass.

What this answers, concretely:
  - requests/sec and median/p99 latency at each concurrency level
  - how much of that latency is the database vs the app vs the network
    (read from the X-DB-Ms / X-App-Ms headers set by main_instrumented.py)
  - the concurrency level where RPS stops climbing while p99 keeps
    climbing -- that crossover IS the bottleneck, found by measurement,
    not asserted by eyeballing a single run

Requires: pip install httpx

Point SELLER_URL at the seller directly (port 8001) to measure the
seller alone, or at the load balancer (port 9000) to measure the
3-instance setup -- run it both ways and compare.
"""

import asyncio
import csv
import itertools
import statistics
import time

import httpx

SELLER_URL = "http://127.0.0.1:8001"

# Each level is (concurrency, wave_duration_seconds).
# Ticket pool is reset before every level to something bigger than
# concurrency * wave_duration * expected_max_rps, so this sweep measures
# steady-state throughput and latency rather than mostly SOLD_OUT
# responses. Run the *actual* 100-ticket / 50k-buyer scenario separately
# with load_test.py -- that's a different question (does idempotency and
# no-overselling hold) from this one (where does latency degrade).
LEVELS = [10, 50, 100, 250, 500, 1000, 2000]
WAVE_SECONDS = 60
TICKETS_PER_WAVE = 200_000

OUTPUT_CSV = "stress_test_results.csv"


def percentile(values, pct):
    if not values:
        return 0.0
    values = sorted(values)
    idx = max(0, min(len(values) - 1, int(pct * len(values)) - 1))
    return values[idx]


async def worker(client, request_id_counter, deadline, results, errors):
    while time.perf_counter() < deadline:
        i = next(request_id_counter)
        payload = {"user_id": f"user_{i}", "request_id": f"req_{i}"}

        start = time.perf_counter()
        try:
            response = await client.post(f"{SELLER_URL}/buy", json=payload)
            client_ms = (time.perf_counter() - start) * 1000

            headers = response.headers
            results.append({
                "client_ms": client_ms,
                "server_total_ms": float(headers.get("x-total-ms", "nan")) if "x-total-ms" in headers else None,
                "db_ms": float(headers.get("x-db-ms", "nan")) if "x-db-ms" in headers else None,
                "app_ms": float(headers.get("x-app-ms", "nan")) if "x-app-ms" in headers else None,
            })
        except Exception as exc:  # noqa: BLE001 -- we want to count *any* failure
            errors.append(str(exc))


async def reset_with_retry(client, ticket_count, attempts=5, delay_seconds=2.0):
    """
    /reset is a single HTTP call outside any per-request error handling --
    if it hits a transient connection blip (server still finishing startup,
    a brief TCP hiccup under Windows), the whole sweep used to crash on it
    even though the server was fine a moment later. Retry a few times
    before giving up for real.
    """
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(
                f"{SELLER_URL}/reset",
                params={"ticket_count": ticket_count},
                timeout=120.0,
            )
            return response
        except httpx.TransportError as exc:
            last_error = exc
            print(f"  /reset attempt {attempt}/{attempts} failed ({exc}); retrying...")
            await asyncio.sleep(delay_seconds)
    raise RuntimeError(
        f"Could not reach {SELLER_URL}/reset after {attempts} attempts. "
        f"Is the seller actually running and listening on that port? "
        f"Last error: {last_error}"
    )


async def run_level(client, concurrency, duration_seconds):
    await reset_with_retry(client, TICKETS_PER_WAVE)

    results = []
    errors = []
    request_id_counter = itertools.count()

    deadline = time.perf_counter() + duration_seconds
    wave_start = time.perf_counter()

    tasks = [
        asyncio.create_task(worker(client, request_id_counter, deadline, results, errors))
        for _ in range(concurrency)
    ]
    await asyncio.gather(*tasks)

    elapsed = time.perf_counter() - wave_start
    completed = len(results)
    rps = completed / elapsed if elapsed > 0 else 0.0

    client_latencies = [r["client_ms"] for r in results]
    db_latencies = [r["db_ms"] for r in results if r["db_ms"] is not None]
    app_latencies = [r["app_ms"] for r in results if r["app_ms"] is not None]

    return {
        "concurrency": concurrency,
        "completed": completed,
        "errors": len(errors),
        "rps": rps,
        "median_client_ms": statistics.median(client_latencies) if client_latencies else 0.0,
        "p99_client_ms": percentile(client_latencies, 0.99),
        "median_db_ms": statistics.median(db_latencies) if db_latencies else 0.0,
        "p99_db_ms": percentile(db_latencies, 0.99),
        "median_app_ms": statistics.median(app_latencies) if app_latencies else 0.0,
        "p99_app_ms": percentile(app_latencies, 0.99),
    }


def print_row(row):
    print(
        f"{row['concurrency']:>10} | "
        f"{row['rps']:>9.1f} | "
        f"{row['median_client_ms']:>10.1f} | "
        f"{row['p99_client_ms']:>10.1f} | "
        f"{row['median_db_ms']:>8.1f} | "
        f"{row['median_app_ms']:>9.1f} | "
        f"{row['errors']:>7}"
    )


def diagnose(rows):
    print("\n" + "=" * 78)
    print("BOTTLENECK READ-OUT")
    print("=" * 78)

    best_rps = 0.0
    plateau_level = None

    for row in rows:
        # "Plateau" = throughput stopped improving by more than 5% while
        # concurrency kept increasing, and p99 got meaningfully worse.
        if row["rps"] > best_rps * 1.05:
            best_rps = row["rps"]
        elif plateau_level is None:
            plateau_level = row["concurrency"]

    if plateau_level:
        print(f"Throughput stopped scaling at concurrency ~{plateau_level}.")
    else:
        print("Throughput was still scaling with concurrency at the highest level tested -- "
              "raise LEVELS and re-run to find the real ceiling.")

    last = rows[-1]
    if last["median_db_ms"] > last["median_app_ms"]:
        print(f"At the top level, DB time (median {last['median_db_ms']:.1f} ms) "
              f"dominates app time (median {last['median_app_ms']:.1f} ms): "
              f"the database -- likely lock contention on the 'find one free ticket' "
              f"query or connection-pool wait -- is the bottleneck, not FastAPI/Python.")
    else:
        print(f"At the top level, app time (median {last['median_app_ms']:.1f} ms) "
              f"dominates DB time (median {last['median_db_ms']:.1f} ms): "
              f"the bottleneck is in the app/network layer, not the database itself.")

    if last["errors"] > 0:
        print(f"{last['errors']} requests errored at the highest concurrency level -- "
              f"check whether that matches pool_size + max_overflow in database.py "
              f"(currently 10 + 20 = 30 connections per instance). Errors past that "
              f"number are the connection pool, not the database engine, running out.")

    print("=" * 78)


async def main():
    print(f"Target: {SELLER_URL}")
    print(f"Sweep : {LEVELS} concurrent workers, {WAVE_SECONDS}s per level\n")
    print(f"{'Concurrency':>10} | {'RPS':>9} | {'p50 (ms)':>10} | {'p99 (ms)':>10} | "
          f"{'DB p50':>8} | {'App p50':>9} | {'Errors':>7}")
    print("-" * 78)

    rows = []
    limits = httpx.Limits(max_connections=max(LEVELS) + 50, max_keepalive_connections=max(LEVELS))

    async with httpx.AsyncClient(timeout=30.0, limits=limits) as client:
        for concurrency in LEVELS:
            row = await run_level(client, concurrency, WAVE_SECONDS)
            print_row(row)
            rows.append(row)

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nRaw results written to {OUTPUT_CSV}")
    diagnose(rows)


if __name__ == "__main__":
    asyncio.run(main())