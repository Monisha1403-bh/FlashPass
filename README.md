FlashPass – Concurrent Ticket Booking System

FlashPass is a backend engineering and architecture project built around a high-contention ticket-sale problem: many buyers compete for a very small fixed inventory at nearly the same time.

The project was developed incrementally: a naive in-memory seller was built first, its race condition was demonstrated with a concurrent buyer, and the design was then evolved into a PostgreSQL-backed seller with database-level locking, idempotency handling, multiple seller instances, load balancing, and latency/bottleneck instrumentation.

Problem Statement

The system must sell a fixed number of tickets while maintaining four invariants under concurrent load:

No overselling – never sell more tickets than exist.

Unique ticket numbers – never issue the same ticket number twice.

Request idempotency – replaying the same request_id must not create a second purchase.

Status consistency – /status must agree with the tickets actually issued.

The buyer/load client must generate concurrent requests, replay duplicate request IDs, measure throughput and latency, and verify the invariants after the run.

Architecture

                         Buyer / Load Client
                                  |
                                  v
                         Load Balancer :9000
                           /      |      \
                          /       |       \
                         v        v        v
                    Seller 1   Seller 2   Seller 3
                     :8001      :8002      :8003
                         \        |        /
                          \       |       /
                           v      v      v
                             PostgreSQL
                               flashpass
                             tickets table

The seller instances share PostgreSQL as the source of truth. Ticket allocation is protected by a PostgreSQL row-level lock rather than a Python process-local lock, which allows the same correctness mechanism to work across independent seller processes.

Technologies

Python

FastAPI

Uvicorn

PostgreSQL

SQLAlchemy

psycopg2

Requests

HTTPX

Pydantic

Repository Structure

FlashPass/
├── .gitignore
├── README.md
├── database.py
├── load_balancer.py
├── main.py
├── main_instrumented.py
├── requirements.txt
├── schema.sql
├── result.txt
└── Buyer/
    ├── load_test.py
    └── stress_test.py

main.py is the normal PostgreSQL-backed seller. main_instrumented.py is the measurement-oriented version used for latency and bottleneck analysis.

API

POST /reset

Starts a fresh sale.

Example:

POST /reset?ticket_count=100

POST /buy

Request:

{
  "user_id": "user_1",
  "request_id": "request_1"
}

The endpoint returns a ticket number when a purchase succeeds, a previously assigned ticket for a replayed request, or a sold-out response.

GET /status

Returns the number of tickets sold/available and the list of ticket ownership.

Database Design

The project uses a tickets table with a primary key on ticket_number and a uniqueness constraint on request_id.

The schema is kept in schema.sql.

CREATE TABLE IF NOT EXISTS tickets (
    ticket_number INTEGER PRIMARY KEY,
    user_id VARCHAR(100),
    request_id VARCHAR(100) UNIQUE,
    sold BOOLEAN DEFAULT FALSE
);

Concurrency Control

The critical ticket-selection query uses PostgreSQL row-level locking:

SELECT ticket_number
FROM tickets
WHERE sold = FALSE
ORDER BY ticket_number
LIMIT 1
FOR UPDATE SKIP LOCKED;

FOR UPDATE locks the selected ticket for the active transaction. SKIP LOCKED allows another concurrent transaction to move on to another available ticket instead of waiting on the locked row.

This is important for the three-seller setup because the lock is held by PostgreSQL rather than by a Python threading.Lock.

Idempotency

Every purchase carries a request_id.

The database schema enforces:

UNIQUE(request_id)

The instrumented implementation also handles a concurrent uniqueness conflict by reading back the request that won the race rather than turning the retry into a second purchase.

This database-level approach is important because a simple:

SELECT request_id
        ↓
   if not found
        ↓
   assign ticket

is not by itself atomic under concurrent duplicate requests.

Naive Implementation Experiment

The project deliberately started with a simple in-memory seller.

A small delay was introduced around the availability check so concurrent requests could expose the race condition. Under load, more successful purchases could be recorded than the available inventory.

That failing experiment established why concurrency control was required before optimizing performance.

Correctness Testing

Buyer/load_test.py is the correctness/load client.

It supports:

configurable concurrent requests

duplicate request IDs

sold-out testing

response/error counting

requests per second

median latency

p99 latency

automated invariant verification

Correctness testing and performance testing are treated as separate concerns:

load_test.py verifies the ticket-sale invariants.

stress_test.py investigates where latency and throughput degrade.

Latency and Bottleneck Instrumentation

main_instrumented.py adds timing headers to responses:

X-Total-Ms
X-DB-Ms
X-App-Ms

These allow the stress client to compare client-observed latency with time measured inside the application and database.

Buyer/stress_test.py is an asynchronous HTTPX-based stress client. It sweeps through concurrency levels and records:

RPS

p50 latency

p99 latency

DB p50

application p50

errors

It also writes raw measurements to:

stress_test_results.csv

Measured Latency Experiment

One recorded single-seller sweep used:

Target: http://127.0.0.1:8001
Concurrency: 10, 50, 100, 250, 500, 1000, 2000
Wave duration: 60 seconds per level

Observed Results

Concurrency

RPS

p50 (ms)

p99 (ms)

10

116.4

50.5

702.2

50

74.4

435.6

3278.9

100

56.2

1271.2

7510.7

250

53.1

3330.0

19487.6

500

49.0

6983.6

40946.3

1000

42.8

13302.8

76748.1

2000

42.2

28519.1

94754.9

The important observation is not simply that latency increased. Throughput decreased as concurrency increased, while p50 and p99 latency rose sharply.

The server-side instrumentation reported very small DB/application times compared with the client-observed latency. The subsequent analysis identified request queuing before the timed route/application section as an important part of the observed delay, rather than treating PostgreSQL itself as the bottleneck without evidence.

These measurements are a development-session result; they should be repeated if the machine, Python/FastAPI/Uvicorn configuration, or code changes.

Slow Datastore Experiment

The project also tested the assignment's ten-second slow-datastore requirement.

An initial attempt inserted a ten-second delay before every SQL statement. That was too broad: /reset, /buy, and /status were all slowed, connection-pool pressure increased, and the test harness itself began failing.

The experiment was then narrowed to the purchase path using PostgreSQL:

SELECT pg_sleep(10);

inside the purchase transaction.

This produces a controlled ten-second datastore delay for the purchase operation without intentionally making /reset and /status unusable.

The slowdown experiment is temporary test instrumentation, not part of the normal production path.

Distributed Seller Setup

The project can run three independent seller processes:

Seller 1 :8001
Seller 2 :8002
Seller 3 :8003
Load Balancer :9000

All sellers point to the same PostgreSQL database.

Example:

uvicorn main_instrumented:app --host 127.0.0.1 --port 8001

uvicorn main_instrumented:app --host 127.0.0.1 --port 8002

uvicorn main_instrumented:app --host 127.0.0.1 --port 8003

Then:

uvicorn load_balancer:app --host 127.0.0.1 --port 9000

The load balancer distributes requests; PostgreSQL remains the shared source of truth.

Configuration

Do not commit PostgreSQL credentials.

Set the database URL through an environment variable.

PowerShell example:

$env:DATABASE_URL="postgresql+psycopg2://postgres:YOUR_PASSWORD@localhost:5432/flashpass"

database.py reads DATABASE_URL from the environment.

Running

1. Install Dependencies

pip install -r requirements.txt

2. Start One Seller

uvicorn main:app --host 127.0.0.1 --port 8001

3. Open Swagger API Documentation

Once the seller is running, open the following URL in your browser:

http://127.0.0.1:8001/docs

The Swagger UI allows you to view and test the available endpoints:

POST /reset
POST /buy
GET  /status

4. Instrumented Testing

For latency and bottleneck testing:

uvicorn main_instrumented:app --host 127.0.0.1 --port 8001

Swagger will then be available at:

http://127.0.0.1:8001/docs

5. Run the Correctness Client

python Buyer\load_test.py

6. Run the Latency Stress Test

python Buyer\stress_test.py

For a first latency sweep, start with a smaller set of concurrency levels before running the full 60-second sweep.

Development Evidence

The repository documents the evolution from:

Naive in-memory seller
        ↓
Race condition / overselling
        ↓
Single-process concurrency protection
        ↓
Idempotency handling
        ↓
PostgreSQL transactions + row locks
        ↓
Multiple seller instances
        ↓
Load balancing
        ↓
Latency instrumentation
        ↓
Stress testing and bottleneck analysis
        ↓
Slow-datastore experiment

The development process was AI-assisted. ChatGPT was used for step-by-step implementation guidance, debugging, test interpretation, and documentation. A separate Claude session was used for the latency/bottleneck instrumentation and stress-testing phase. The actual commands were run locally and the recorded measurements came from those local experiments.

Key Engineering Lessons

Correctness must be established before optimizing throughput.

Application-level locks do not solve coordination across independent processes.

PostgreSQL row-level locking can provide a shared concurrency-control mechanism.

Idempotency must be enforced atomically under concurrent retries.

Client-observed latency can include queueing that is invisible to route-level timing.

Increasing concurrency can eventually reduce throughput while dramatically increasing tail latency.

A slow datastore can dominate request completion time even when application code itself is unchanged.

Performance claims should be supported by measurements rather than assumptions.

Conclusion

FlashPass demonstrates the progression from a race-prone ticket seller to a database-backed concurrent and distributed design, together with a load client that verifies correctness and a separate instrumentation layer that measures where latency is introduced.

The project intentionally keeps correctness and performance analysis separate: first prove that no ticket is oversold or duplicated, then investigate how the system behaves as concurrency increases.
