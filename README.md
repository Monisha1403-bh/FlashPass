# FlashPass – Concurrent Ticket Booking System

FlashPass is a backend ticket booking system designed to demonstrate
concurrency control, request idempotency, database locking, distributed
ticket sellers, load balancing, and performance testing.

The system sells a fixed number of tickets and guarantees that the same
ticket is never assigned to multiple users, even when many requests arrive
concurrently.

---

## Features

- Fixed ticket inventory
- Ticket purchase API
- Ticket inventory reset
- Ticket status and ownership API
- Request idempotency using `request_id`
- PostgreSQL-backed persistent state
- Row-level database locking
- Multiple independent seller instances
- Round-robin load balancing
- Concurrent load testing
- Requests-per-second measurement
- Median latency measurement
- P99 latency measurement
- Automated invariant verification
- Slow-datastore experiment

---

## System Architecture

```text
                         Buyer
                           |
                           v
                  Load Balancer :9000
                           |
             +-------------+-------------+
             |             |             |
             v             v             v
        Seller 1       Seller 2       Seller 3
         :8001          :8002          :8003
             |             |             |
             +-------------+-------------+
                           |
                           v
                      PostgreSQL
                        flashpass
                           |
                     tickets table