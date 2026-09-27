# FlashPass – Concurrent Ticket Booking System

FlashPass is a backend engineering and architecture project built around a high-contention ticket-sale problem: many buyers compete for a very small fixed inventory at nearly the same time.

The project was developed incrementally. I first built a naive in-memory seller and used a concurrent buyer to demonstrate its race condition. I then moved the correctness mechanism into PostgreSQL, added database-level locking and idempotency handling, extended the system to multiple independent seller processes, and added latency/bottleneck instrumentation.

The repository also contains the development journal and decision notes so that the implementation, experiments, failures, and reasoning can be reviewed alongside the final code.

---

## 1. Clean Checkout → Running System

The following procedure is intended to work from a fresh clone.

### Prerequisites

- Python 3.10+
- PostgreSQL
- Git

### 1. Clone the repository

```powershell
git clone https://github.com/Monisha1403-bh/FlashPass.git
cd FlashPass
```

### 2. Create and activate a Python virtual environment

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```powershell
pip install -r requirements.txt
```

### 4. Create the PostgreSQL database

Open PostgreSQL SQL Shell (`psql`) or pgAdmin and create:

```sql
CREATE DATABASE flashpass;
```

### 5. Configure the database connection

Do not commit PostgreSQL credentials to the repository.

In PowerShell:

```powershell
$env:DATABASE_URL="postgresql+psycopg2://postgres:YOUR_PASSWORD@localhost:5432/flashpass"
```

Replace `YOUR_PASSWORD` with the local PostgreSQL password.

### 6. Initialize the database schema

If `psql` is available in PATH:

```powershell
psql "$env:DATABASE_URL" -f schema.sql
```

If `psql` is not available in PATH, open PostgreSQL SQL Shell and run the SQL contained in `schema.sql`.

The schema creates the `tickets` table with:
- `ticket_number` as the primary key
- `request_id` as a unique value
- `sold` as the ticket state

### 7. Start the normal seller

```powershell
uvicorn main:app --host 127.0.0.1 --port 8001
```

The server should report:

```text
Uvicorn running on http://127.0.0.1:8001
```

### 8. Open Swagger

Open this URL in a browser:

**http://127.0.0.1:8001/docs**

Swagger UI provides interactive access to:

- `POST /reset`
- `POST /buy`
- `GET /status`

### 9. Run the correctness/load test

Keep the seller running and open a second terminal in the project directory:

```powershell
.venv\Scripts\activate
python Buyer/load_test.py
```

The buyer generates concurrent requests, replays duplicate request IDs, checks sold-out behavior, and verifies the four ticket-sale invariants.

---

## 2. Problem Statement

The system must sell a fixed number of tickets while maintaining four invariants under concurrent load:

1. **No overselling** – never sell more tickets than exist.
2. **Unique ticket numbers** – never issue the same ticket number twice.
3. **Request idempotency** – replaying the same `request_id` must not create a second purchase.
4. **Status consistency** – `/status` must agree with the tickets actually issued.

The buyer/load client must generate concurrent requests, replay duplicate request IDs, measure throughput and latency, and verify the invariants after the run.

---

## 3. Architecture

```text
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
```

The seller instances share PostgreSQL as the source of truth.

Ticket allocation is protected by a PostgreSQL row-level lock rather than a Python process-local lock. This means the correctness mechanism can work across independent seller processes.

---

## 4. Repository Structure

```text
FlashPass/
├── Buyer/
│   ├── load_test.py
│   └── stress_test.py
├── logs/
│   └── FlashPass_AI_Development_Journal.pdf
├── .gitignore
├── DECISIONS.md
├── README.md
├── database.py
├── load_balancer.py
├── main.py
├── main_instrumented.py
├── requirements.txt
├── result.txt
└── schema.sql
```

### Important files

- `main.py` – normal PostgreSQL-backed seller.
- `main_instrumented.py` – measurement-oriented seller used for latency and bottleneck analysis.
- `database.py` – creates the SQLAlchemy PostgreSQL engine using `DATABASE_URL`.
- `schema.sql` – database schema.
- `Buyer/load_test.py` – correctness/load client.
- `Buyer/stress_test.py` – asynchronous latency/stress client.
- `load_balancer.py` – load-balancing layer for multiple seller processes.
- `result.txt` – recorded experiment results.
- `DECISIONS.md` – engineering decisions, rejected approaches, and reasons.
- `logs/` – AI-assisted development session journal.

---

# 5. API

## POST /reset

Starts a fresh sale.

Example:

```text
POST /reset?ticket_count=100
```

This resets the ticket state for the requested inventory.

## POST /buy

Request:

```json
{
  "user_id": "user_1",
  "request_id": "request_1"
}
```

The endpoint returns:
- a ticket number when a purchase succeeds,
- the previously assigned ticket when the same request is replayed,
- or a sold-out response when no tickets remain.

## GET /status

Returns:
- number of tickets sold,
- number of tickets available,
- ticket ownership.

---

# 6. Database Design

The project uses a `tickets` table with a primary key on `ticket_number` and a uniqueness constraint on `request_id`.

The schema is kept in `schema.sql`.

```sql
CREATE TABLE IF NOT EXISTS tickets (
    ticket_number INTEGER PRIMARY KEY,
    user_id VARCHAR(100),
    request_id VARCHAR(100) UNIQUE,
    sold BOOLEAN DEFAULT FALSE
);
```

The database is the shared source of truth for all seller instances.

---

# 7. Concurrency Control: Decision and Reasoning

## The first approach: Python process-local locking

The first corrected implementation used a Python `threading.Lock`.

That solved the race condition inside one seller process, but it created an architectural limitation: a Python lock exists only inside that process.

Once multiple independent seller processes are introduced, Seller 1 and Seller 2 cannot coordinate through the same Python lock.

Because the system can have three independent sellers, I moved the critical concurrency control into PostgreSQL.

## The PostgreSQL approach

The critical ticket-selection query uses:

```sql
SELECT ticket_number
FROM tickets
WHERE sold = FALSE
ORDER BY ticket_number
LIMIT 1
FOR UPDATE SKIP LOCKED;
```

`FOR UPDATE` locks the selected ticket for the active transaction.

`SKIP LOCKED` allows another concurrent transaction to move to another available ticket instead of waiting for the already-locked row.

The important design decision was therefore:

```text
Process-local lock
       ↓
works for one process
       ↓
does not coordinate independent sellers

PostgreSQL row-level lock
       ↓
shared by all seller processes
       ↓
supports the distributed seller setup
```

This decision is documented in more detail in `DECISIONS.md`.

---

# 8. Idempotency: Decision and Race Condition

Every purchase carries a `request_id`.

The database schema enforces:

```sql
UNIQUE(request_id)
```

However, a simple:

```text
SELECT request_id
      ↓
if not found
      ↓
assign ticket
```

is not atomic under concurrent duplicate requests.

Two identical requests can reach the check before either one has recorded its request.

The instrumented implementation therefore relies on the database uniqueness constraint and handles a concurrent uniqueness conflict by reading back the request that won the race rather than creating a second purchase.

This is an important distinction between:

```text
checking for duplicates
```

and:

```text
atomically enforcing idempotency
```

---

# 9. Naive Implementation Experiment

The project deliberately started with a simple in-memory seller.

A small delay was introduced around the availability check so that concurrent requests could expose the race condition.

A concurrent buyer was then used to attack the seller.

The naive implementation produced more successful purchases than the available inventory, demonstrating overselling.

The purpose of this experiment was not to keep the naive design. It was to establish a measurable failure before introducing concurrency control.

The sequence was:

```text
Naive implementation
        ↓
Concurrent test
        ↓
Race condition / overselling
        ↓
Identify shared-state problem
        ↓
Introduce concurrency control
```

The failing run and subsequent passing runs are recorded in `result.txt`.

---

# 10. Correctness Testing

`Buyer/load_test.py` is the correctness/load client.

It supports:

- configurable concurrent requests
- duplicate request IDs
- sold-out testing
- response/error counting
- requests per second
- median latency
- p99 latency
- automated invariant verification

Correctness testing and performance testing are treated as separate concerns:

```text
load_test.py
    ↓
Does the system remain correct?

stress_test.py
    ↓
Where does latency/throughput degrade?
```

This separation prevents a performance measurement from being mistaken for a correctness result.

---

# 11. Recorded Correctness Evidence

The development results include both failing and passing experiments.

The initial naive implementation demonstrated overselling under concurrent load.

After adding concurrency control and idempotency handling, the PostgreSQL-backed seller passed the four invariants in recorded load tests.

Example PostgreSQL baseline measurements included:

- 1100 requests
- 100 concurrent workers
- 100 new purchases
- duplicate request replays
- sold-out responses
- 0 errors
- all four invariants passing

The detailed recorded measurements are kept in `result.txt`.

---

# 12. Latency and Bottleneck Instrumentation

`main_instrumented.py` adds timing headers:

```text
X-Total-Ms
X-DB-Ms
X-App-Ms
```

These allow the stress client to compare:

- client-observed request latency
- time measured inside the application
- time measured around database operations

`Buyer/stress_test.py` is an asynchronous HTTPX-based stress client.

It sweeps through concurrency levels and records:

- RPS
- p50 latency
- p99 latency
- DB p50
- application p50
- errors

It also writes raw measurements to:

```text
stress_test_results.csv
```

---

# 13. Measured Latency Experiment

One recorded single-seller sweep used:

```text
Target:      http://127.0.0.1:8001
Concurrency: 10, 50, 100, 250, 500, 1000, 2000
Duration:    60 seconds per level
```

## Observed results

| Concurrency | RPS | p50 (ms) | p99 (ms) |
|---:|---:|---:|---:|
| 10 | 116.4 | 50.5 | 702.2 |
| 50 | 74.4 | 435.6 | 3278.9 |
| 100 | 56.2 | 1271.2 | 7510.7 |
| 250 | 53.1 | 3330.0 | 19487.6 |
| 500 | 49.0 | 6983.6 | 40946.3 |
| 1000 | 42.8 | 13302.8 | 76748.1 |
| 2000 | 42.2 | 28519.1 | 94754.9 |

The important observation is not simply that latency increased.

As concurrency increased:

- throughput decreased,
- median latency increased sharply,
- tail latency increased even more sharply.

The server-side instrumentation reported much smaller DB/application times than the client-observed latency.

Therefore, I did not conclude that PostgreSQL itself was the bottleneck from these measurements.

Instead, the measurements pointed toward request queuing before the timed route/application section as an important contributor to the observed delay.

This distinction matters because client-observed latency and database execution time are not automatically the same thing.

These measurements are development-session results and should be repeated if the machine, Python/FastAPI/Uvicorn configuration, or code changes.

---

# 14. Slow Datastore Experiment

The assignment also required investigation of a ten-second slow datastore.

## First attempt — rejected

The first approach inserted a ten-second delay before every SQL statement.

This was intentionally tested, but it was too broad.

It slowed:

- `/reset`
- `/buy`
- `/status`

and increased connection-pool pressure until the test harness began failing.

I rejected this approach because it did not isolate the behavior I wanted to measure.

## Revised experiment

The experiment was narrowed to the purchase transaction using:

```sql
SELECT pg_sleep(10);
```

inside the purchase path.

This creates a controlled ten-second datastore delay for the purchase operation while leaving `/reset` and `/status` outside the artificial delay.

The slowdown instrumentation is temporary test instrumentation and is not part of the normal production path.

This experiment is documented in the development journal and decision notes.

---

# 15. Distributed Seller Setup

The project can run three independent seller processes:

```text
Seller 1       :8001
Seller 2       :8002
Seller 3       :8003
Load Balancer  :9000
```

All sellers point to the same PostgreSQL database.

Start the sellers:

```powershell
uvicorn main_instrumented:app --host 127.0.0.1 --port 8001
```

```powershell
uvicorn main_instrumented:app --host 127.0.0.1 --port 8002
```

```powershell
uvicorn main_instrumented:app --host 127.0.0.1 --port 8003
```

Then start the load balancer:

```powershell
uvicorn load_balancer:app --host 127.0.0.1 --port 9000
```

The load balancer distributes requests.

PostgreSQL remains the shared source of truth.

The important property is that correctness does not depend on a lock shared between Python processes.

---

# 16. Instrumented Testing

To run the latency/bottleneck version instead of the normal seller:

```powershell
uvicorn main_instrumented:app --host 127.0.0.1 --port 8001
```

Swagger:

```text
http://127.0.0.1:8001/docs
```

Then run:

```powershell
python Buyer/stress_test.py
```

For a first run, use a smaller concurrency sweep before running the full 60-second experiment.

---

# 17. Development Decisions

The project contains `DECISIONS.md`.

It records the important design choices and rejected approaches, including:

- why a Python process-local lock was not sufficient for multiple sellers
- why PostgreSQL row-level locking was selected
- why `SKIP LOCKED` was used
- why database uniqueness is required for idempotency
- why the first ten-second SQL delay experiment was rejected
- why the latency results were not interpreted as proof of a PostgreSQL bottleneck

The purpose of this file is to record not only what was implemented, but why particular approaches were accepted or rejected.

---

# 18. AI-Assisted Development and Session Logs

The project was developed with AI assistance, but the commands, tests, measurements, and final decisions were performed and verified locally.

### ChatGPT

ChatGPT was used for:

- step-by-step implementation guidance
- debugging
- interpreting test failures
- explaining concurrency/database behavior
- documentation assistance

### Claude

A separate Claude session was used during the latency/bottleneck instrumentation and stress-testing phase.

The session helped develop and analyze the instrumentation and stress-test approach.

### Candidate verification and decision-making

The AI suggestions were not treated as automatically correct.

The development process included testing proposed approaches and changing them when the measurements showed that they were not isolating the intended behavior.

For example, the first slow-database experiment delayed every SQL statement. The resulting connection-pool and test-harness failures showed that the experiment was too broad, so the experiment was changed to delay only the purchase transaction.

Similarly, the latency measurements were examined by comparing client-observed latency with server-side DB/application timings. Because the measurements did not support the claim that PostgreSQL was the dominant bottleneck, that conclusion was not made.

The complete AI-assisted development journal is stored in:

```text
logs/FlashPass_AI_Development_Journal.pdf
```

The journal contains the development process, debugging, failed experiments, test results, and AI-assisted sessions.

---

# 19. Known Limitations and Weaknesses

The following limitations are intentionally stated rather than hidden:

### 1. Performance measurements are machine-dependent

The recorded RPS and latency values came from a local development machine. They should not be treated as universal capacity numbers.

### 2. Route-level instrumentation does not capture all request queuing

The stress experiment showed a large difference between client-observed latency and the time measured inside the route/application instrumentation.

Therefore, the instrumentation does not explain every millisecond of end-to-end latency.

### 3. The slow-datastore experiment is artificial

`pg_sleep(10)` is test instrumentation used to model a slow datastore. It is not a production database behavior.

### 4. Reset is a test/setup operation

`/reset` is intended to start a fresh sale before a test. It is not designed to be called concurrently with an active sale.

### 5. Distributed deployment is demonstrated locally

The three-seller setup runs as multiple local processes sharing one PostgreSQL instance. It demonstrates the concurrency/control architecture but is not a production cloud deployment.

These limitations define what the current measurements and implementation do—and do not—demonstrate.

---

# 20. Development Evidence

The implementation evolved through measurable failures and corrections:

```text
Naive in-memory seller
        ↓
Concurrent race / overselling
        ↓
Single-process concurrency protection
        ↓
Idempotency handling
        ↓
PostgreSQL transactions + row-level locking
        ↓
Multiple seller processes
        ↓
Load balancing
        ↓
Latency instrumentation
        ↓
Stress testing
        ↓
Bottleneck analysis
        ↓
Slow-datastore experiment
        ↓
Rejected overly-broad slowdown
        ↓
Controlled purchase-path slowdown
```

The key point is that the system was not designed once and assumed to be correct.

The implementation was changed in response to observed failures and measurements.

---

# 21. Key Engineering Lessons

### Correctness before optimization

The naive implementation was intentionally tested first so that the concurrency failure could be observed before optimizing the system.

### Locks must exist at the correct scope

A process-local lock can coordinate threads inside one process but cannot coordinate independent seller processes.

### Database constraints are part of correctness

A unique database constraint is stronger than relying only on application-level duplicate checks.

### End-to-end latency needs measurement at multiple layers

Client-observed latency can include queueing that is not visible inside route-level timing.

### More concurrency does not automatically mean more throughput

The recorded stress test showed decreasing throughput and rapidly increasing tail latency at higher concurrency.

### Failed experiments are useful evidence

The first ten-second delay experiment was too broad. Its failure led to a more controlled experiment.

### Performance claims should be supported by measurements

The bottleneck analysis deliberately distinguishes measured evidence from assumptions.

---

# 22. Running Summary

### Normal seller

```powershell
uvicorn main:app --host 127.0.0.1 --port 8001
```

Swagger:

```text
http://127.0.0.1:8001/docs
```

Correctness:

```powershell
python Buyer/load_test.py
```

### Instrumented seller

```powershell
uvicorn main_instrumented:app --host 127.0.0.1 --port 8001
```

Stress test:

```powershell
python Buyer/stress_test.py
```

### Three sellers + load balancer

```text
Seller 1       http://127.0.0.1:8001
Seller 2       http://127.0.0.1:8002
Seller 3       http://127.0.0.1:8003
Load Balancer  http://127.0.0.1:9000
```

---

# 23. Conclusion

FlashPass demonstrates the progression from a race-prone ticket seller to a PostgreSQL-backed concurrent and distributed design, together with a load client that verifies correctness and a separate instrumentation layer that measures where latency is introduced.

The project intentionally separates:

```text
Correctness
    ↓
Does the system maintain its invariants?

Performance
    ↓
How does the system behave as concurrency increases?

Diagnosis
    ↓
Where is the observed latency actually coming from?
```

The repository therefore contains not only the final implementation, but also the experiments, measurements, rejected approaches, limitations, engineering decisions, and development journal used to arrive at it.
