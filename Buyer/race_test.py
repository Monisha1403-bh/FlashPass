"""
Race test for idempotency specifically.

load_test.py sends its duplicate request_ids *after* all the original
requests -- by the time they're scheduled, the originals have almost
always already committed, so it rarely catches the actual race. This
script instead takes N distinct request_ids and fires DUPLICATES_PER_ID
copies of *each one*, all shuffled together and launched at the same
moment, so genuine duplicates are genuinely concurrent.

If invariant 3 fails, you'll see a request_id that ended up owning more
than one ticket_number -- which the naive check-then-act version
(main.py without the UNIQUE constraint / conflict handling) can produce,
and main_instrumented.py should not.

Run it once against the unfixed seller (main.py) to get a FAIL, then
against main_instrumented.py to get a PASS -- that's your before/after
pair for the write-up.

Requires: pip install httpx
"""

import asyncio
import random
import httpx

SELLER_URL = "http://127.0.0.1:8001"

TICKET_COUNT = 100
DISTINCT_REQUEST_IDS = 40      # how many different request_ids to test
DUPLICATES_PER_ID = 20         # how many concurrent copies of each one


async def fire(client, user_id, request_id, results):
    try:
        response = await client.post(
            f"{SELLER_URL}/buy",
            json={"user_id": user_id, "request_id": request_id},
        )
        data = response.json()
        results.append((request_id, data.get("ticket_number")))
    except Exception as exc:  # noqa: BLE001
        results.append((request_id, f"ERROR: {exc}"))


async def main():
    print(f"Target: {SELLER_URL}")
    print(f"{DISTINCT_REQUEST_IDS} request_ids x {DUPLICATES_PER_ID} concurrent copies each\n")

    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.post(f"{SELLER_URL}/reset", params={"ticket_count": TICKET_COUNT})

        # Build every (user_id, request_id) call up front, then shuffle so
        # duplicates of different request_ids are interleaved with each
        # other rather than sent id-by-id -- closer to real concurrent
        # traffic than looping one request_id at a time.
        calls = []
        for i in range(DISTINCT_REQUEST_IDS):
            request_id = f"race_req_{i}"
            for j in range(DUPLICATES_PER_ID):
                calls.append((f"race_user_{i}_{j}", request_id))

        random.shuffle(calls)

        results = []
        tasks = [
            asyncio.create_task(fire(client, user_id, request_id, results))
            for user_id, request_id in calls
        ]
        await asyncio.gather(*tasks)

    # Group ticket_numbers returned per request_id
    tickets_per_request = {}
    for request_id, ticket_number in results:
        tickets_per_request.setdefault(request_id, set()).add(ticket_number)

    violations = {
        request_id: tickets
        for request_id, tickets in tickets_per_request.items()
        if len(tickets) > 1
    }

    # A violation entry can mix a real int ticket_number with an "ERROR: ..."
    # string (a request that crashed instead of returning cleanly) -- both
    # are worth reporting, but they mean different things, so split them out
    # rather than trying to sort mixed types together.
    def describe(tickets):
        real_tickets = sorted(t for t in tickets if isinstance(t, int))
        errors = [t for t in tickets if not isinstance(t, int)]
        parts = []
        if real_tickets:
            parts.append(f"tickets {real_tickets}")
        if errors:
            parts.append(f"{len(errors)} error response(s): {errors[0]}"
                         + (" (+more)" if len(errors) > 1 else ""))
        return " and ".join(parts)

    print("=" * 60)
    print("RACE TEST RESULT")
    print("=" * 60)
    print(f"Distinct request_ids tested : {len(tickets_per_request)}")
    print(f"Violations (>1 distinct result for the same request_id): {len(violations)}")

    if violations:
        print("\nFAIL -- these request_ids did not resolve to one consistent ticket:")
        for request_id, tickets in violations.items():
            print(f"  {request_id} -> {describe(tickets)}")
        print("\n(A mix of a real ticket number AND an error usually means the")
        print(" database's unique constraint correctly rejected a duplicate write,")
        print(" but the app crashed instead of catching it gracefully -- check the")
        print(" seller's terminal for an IntegrityError traceback.)")
    else:
        print("\nPASS -- every request_id resolved to exactly one ticket_number,")
        print("even with all its duplicates fired at the same instant.")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())