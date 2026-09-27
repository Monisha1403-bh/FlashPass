# FlashPass – Engineering Decisions

## 1. Architecture Chosen

FlashPass evolved from a simple in-memory ticket seller into a PostgreSQL-backed
multi-instance system.

The final architecture consists of:

```text
Buyer / Load Tester
        |
        v
   Load Balancer
        |
   +----+----+
   |    |    |
Seller Seller Seller
  :8001 :8002 :8003
   |    |    |
   +----+----+
        |
        v
    PostgreSQL