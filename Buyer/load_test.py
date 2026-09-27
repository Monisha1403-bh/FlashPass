import requests
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

SELLER_URL = "http://127.0.0.1:9000"

TICKET_COUNT = 100
TOTAL_REQUESTS = 1000
CONCURRENCY = 100
DUPLICATE_COUNT = 0


def buy_ticket(user_id, request_id):
    start = time.perf_counter()

    try:
        response = requests.post(
            f"{SELLER_URL}/buy",
            json={
                "user_id": user_id,
                "request_id": request_id
            },
            timeout=30
        )

        latency = time.perf_counter() - start

        data = response.json()

        return {
            "user_id": user_id,
            "request_id": request_id,
            "status_code": response.status_code,
            "response": data,
            "latency": latency
        }

    except Exception as e:

        latency = time.perf_counter() - start

        return {
            "user_id": user_id,
            "request_id": request_id,
            "status_code": 0,
            "response": {},
            "latency": latency,
            "error": str(e)
        }


def main():

    print("=" * 50)
    print("       FLASH PASS LOAD TEST")
    print("=" * 50)

    # ------------------------------------------------
    # STEP 1: RESET SELLER
    # ------------------------------------------------

    print("\nResetting seller...")

    reset_response = requests.post(
        f"{SELLER_URL}/reset",
        params={"ticket_count": TICKET_COUNT}
    )

    print("Reset response:", reset_response.json())

    # ------------------------------------------------
    # STEP 2: CREATE REQUESTS
    # ------------------------------------------------

    requests_to_send = []

    # Normal unique requests
    for i in range(TOTAL_REQUESTS):

        requests_to_send.append({
            "user_id": f"user_{i}",
            "request_id": f"request_{i}"
        })

    # Duplicate requests
    for i in range(DUPLICATE_COUNT):

        requests_to_send.append({
            "user_id": f"user_{i}",
            "request_id": f"request_{i}"
        })

    total_sent = len(requests_to_send)

    print(f"\nSending {total_sent} requests...")
    print(f"Normal requests : {TOTAL_REQUESTS}")
    print(f"Duplicate retries: {DUPLICATE_COUNT}")
    print(f"Concurrency      : {CONCURRENCY}")

    # ------------------------------------------------
    # STEP 3: SEND REQUESTS
    # ------------------------------------------------

    results = []

    test_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:

        futures = []

        for request in requests_to_send:

            future = executor.submit(
                buy_ticket,
                request["user_id"],
                request["request_id"]
            )

            futures.append(future)

        for future in as_completed(futures):

            result = future.result()

            results.append(result)

    total_time = time.perf_counter() - test_start

    # ------------------------------------------------
    # STEP 4: CLASSIFY RESPONSES
    # ------------------------------------------------

    new_purchases = []
    duplicate_replays = []
    sold_out = []
    errors = []

    for result in results:

        response = result["response"]

        if result["status_code"] != 200:

            errors.append(result)

        elif response.get("message") == "Ticket purchased":

            new_purchases.append(result)

        elif response.get("message") == "Request already processed":

            duplicate_replays.append(result)

        elif response.get("message") == "SOLD OUT":

            sold_out.append(result)

        else:

            errors.append(result)

    # ------------------------------------------------
    # STEP 5: LATENCY
    # ------------------------------------------------

    latencies = [
        result["latency"]
        for result in results
        if result["status_code"] != 0
    ]

    if latencies:

        median_latency = statistics.median(latencies)

        sorted_latencies = sorted(latencies)

        p99_index = int(0.99 * len(sorted_latencies)) - 1

        p99_index = max(0, p99_index)

        p99_latency = sorted_latencies[p99_index]

    else:

        median_latency = 0
        p99_latency = 0

    requests_per_second = (
        total_sent / total_time
        if total_time > 0
        else 0
    )

    # ------------------------------------------------
    # STEP 6: GET SELLER STATUS
    # ------------------------------------------------

    status_response = requests.get(
        f"{SELLER_URL}/status"
    )

    status = status_response.json()

    tickets = status.get("tickets", [])

    tickets_sold = status.get(
        "tickets_sold",
        0
    )

    tickets_available = status.get(
        "tickets_available",
        0
    )

    # ------------------------------------------------
    # STEP 7: INVARIANT CHECKS
    # ------------------------------------------------

    ticket_numbers = [
        ticket["ticket_number"]
        for ticket in tickets
    ]

    # Invariant 1:
    # Never sell more tickets than exist.

    invariant_1 = (
        tickets_sold <= TICKET_COUNT
        and len(tickets) <= TICKET_COUNT
    )

    # Invariant 2:
    # Never issue the same ticket number twice.

    invariant_2 = (
        len(ticket_numbers)
        == len(set(ticket_numbers))
    )

    # Invariant 3:
    # Same request ID must always return
    # the same ticket.

    request_to_tickets = {}

    for result in results:

        response = result["response"]

        if "ticket_number" not in response:
            continue

        request_id = result["request_id"]

        ticket_number = response["ticket_number"]

        if request_id not in request_to_tickets:

            request_to_tickets[request_id] = []

        request_to_tickets[request_id].append(
            ticket_number
        )

    invariant_3 = True

    duplicate_request_problems = []

    for request_id, ticket_list in request_to_tickets.items():

        unique_tickets = set(ticket_list)

        if len(unique_tickets) != 1:

            invariant_3 = False

            duplicate_request_problems.append(
                (request_id, ticket_list)
            )

    # Invariant 4:
    # Status must match actual issued tickets.

    invariant_4 = (
        tickets_sold == len(tickets)
        and tickets_sold + tickets_available
        == TICKET_COUNT
    )

    # ------------------------------------------------
    # STEP 8: PRINT RESULTS
    # ------------------------------------------------

    print("\n" + "=" * 50)
    print("             LOAD TEST RESULT")
    print("=" * 50)

    print(f"Requests sent       : {total_sent}")
    print(f"New purchases       : {len(new_purchases)}")
    print(f"Duplicate replays   : {len(duplicate_replays)}")
    print(f"Sold out responses  : {len(sold_out)}")
    print(f"Errors              : {len(errors)}")

    print(f"Total time          : {total_time:.3f} seconds")
    print(f"Requests/sec        : {requests_per_second:.2f}")
    print(f"Median latency      : {median_latency * 1000:.2f} ms")
    print(f"P99 latency         : {p99_latency * 1000:.2f} ms")

    # ------------------------------------------------
    # INVARIANTS
    # ------------------------------------------------

    print("\n" + "=" * 50)
    print("             INVARIANT CHECK")
    print("=" * 50)

    print(
        "1. No overselling        :",
        "PASS" if invariant_1 else "FAIL"
    )

    print(
        "2. Unique ticket numbers :",
        "PASS" if invariant_2 else "FAIL"
    )

    print(
        "3. Request idempotency   :",
        "PASS" if invariant_3 else "FAIL"
    )

    print(
        "4. Status consistency    :",
        "PASS" if invariant_4 else "FAIL"
    )

    # ------------------------------------------------
    # SELLER STATUS
    # ------------------------------------------------

    print("\n" + "=" * 50)
    print("             SELLER STATUS")
    print("=" * 50)

    print(f"Tickets sold      : {tickets_sold}")
    print(f"Tickets available : {tickets_available}")
    print(f"Tickets in status : {len(tickets)}")

    # ------------------------------------------------
    # DUPLICATE REQUEST PROBLEMS
    # ------------------------------------------------

    if duplicate_request_problems:

        print("\nDuplicate request violations:")

        for request_id, ticket_list in duplicate_request_problems:

            print(
                f"{request_id} -> {ticket_list}"
            )

    # ------------------------------------------------
    # OVERALL RESULT
    # ------------------------------------------------

    overall_pass = (
    len(errors) == 0
    and invariant_1
    and invariant_2
    and invariant_3
    and invariant_4
)

    print("\n" + "=" * 50)

    if overall_pass:

        print("             OVERALL: PASS")

    else:

        print("             OVERALL: FAIL")

    print("=" * 50)


if __name__ == "__main__":
    main()