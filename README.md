# FlashPass – Concurrent Ticket Booking System

FlashPass is a backend engineering project that demonstrates how to build a
concurrent ticket-selling system capable of handling a large number of
simultaneous purchase requests while maintaining correctness.

The system was designed around the following challenge:

> 50,000 people want 100 tickets, and all requests may arrive within the same
> 60-second window.

The project focuses on concurrency control, race conditions, idempotency,
database transactions, distributed seller instances, load balancing, and
performance testing.

---

# 1. Problem Statement

The goal is to build a ticket seller that sells a fixed number of tickets
without violating important correctness guarantees even when many users
attempt to purchase tickets concurrently.

The system must guarantee:

1. Never sell more tickets than exist.
2. Never issue the same ticket number twice.
3. If the same `request_id` is received multiple times, it must not create
   multiple purchases.
4. The `/status` endpoint must accurately reflect the tickets actually issued.

A separate buyer/load-testing client is used to generate concurrent requests
and verify these invariants.

---

# 2. Project Objectives

The project was developed incrementally to understand and solve concurrency
problems.

The main objectives were:

- Build a basic ticket-selling API.
- Demonstrate a race condition using a naive implementation.
- Use concurrency control to eliminate overselling.
- Add request idempotency.
- Move ticket state from application memory to PostgreSQL.
- Use database transactions and row-level locking.
- Run multiple independent seller instances.
- Add a load balancer.
- Build a concurrent buyer/load-testing client.
- Measure throughput and latency.
- Measure median and P99 latency.
- Simulate datastore slowdown.
- Verify correctness automatically under load.

---

# 3. System Architecture

The final architecture consists of a buyer/load-testing client, a load
balancer, multiple seller instances, and a shared PostgreSQL database.

```text
                         Buyer / Load Test
                                |
                                v
                    +-----------------------+
                    |    Load Balancer      |
                    |       :9000            |
                    +-----------+-----------+
                                |
             +------------------+------------------+
             |                  |                  |
             v                  v                  v
       +-----------+      +-----------+      +-----------+
       | Seller 1  |      | Seller 2  |      | Seller 3  |
       |   :8001   |      |   :8002   |      |   :8003   |
       +-----+-----+      +-----+-----+      +-----+-----+
             |                  |                  |
             +------------------+------------------+
                                |
                                v
                    +-----------------------+
                    |      PostgreSQL       |
                    |       flashpass       |
                    +-----------+-----------+
                                |
                                v
                         tickets table