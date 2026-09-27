import os
import time
import contextvars

from fastapi import FastAPI, Request
from pydantic import BaseModel
from sqlalchemy import text, event
from sqlalchemy.exc import IntegrityError

from database import engine

SERVER_NAME = os.getenv("SERVER_NAME", "seller-default")

app = FastAPI(title="FlashPass Ticket Seller")


class BuyRequest(BaseModel):
    user_id: str
    request_id: str


# ============================================================
# LATENCY BREAKDOWN INSTRUMENTATION
#
# The load client only sees "total time for one HTTP call". That number
# is (network) + (FastAPI/Python overhead) + (time actually spent in
# Postgres). To find the bottleneck you need those split apart, not
# guessed at. This listens to every SQL statement SQLAlchemy runs on
# this connection and adds up how many milliseconds were spent inside
# the database during the current request. The app then reports it back
# in a response header so the client can log it per-request.
# ============================================================

_db_time_ms = contextvars.ContextVar("_db_time_ms", default=0.0)


@event.listens_for(engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._query_start_time = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    elapsed_ms = (time.perf_counter() - context._query_start_time) * 1000
    _db_time_ms.set(_db_time_ms.get() + elapsed_ms)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    _db_time_ms.set(0.0)
    request_start = time.perf_counter()

    response = await call_next(request)

    total_ms = (time.perf_counter() - request_start) * 1000
    db_ms = _db_time_ms.get()
    app_ms = total_ms - db_ms

    response.headers["X-Server-Name"] = SERVER_NAME
    response.headers["X-Total-Ms"] = f"{total_ms:.3f}"
    response.headers["X-DB-Ms"] = f"{db_ms:.3f}"
    response.headers["X-App-Ms"] = f"{app_ms:.3f}"

    return response


@app.get("/")
def home():
    return {
        "message": "FlashPass Seller is running",
        "server": SERVER_NAME
    }


@app.post("/reset")
def reset(ticket_count: int):

    with engine.begin() as connection:

        connection.execute(
            text("""
                UPDATE tickets
                SET user_id = NULL,
                    request_id = NULL,
                    sold = FALSE
            """)
        )

        connection.execute(
            text("""
                DELETE FROM tickets
                WHERE ticket_number > :ticket_count
            """),
            {"ticket_count": ticket_count}
        )

        current_count = connection.execute(
            text("SELECT COUNT(*) FROM tickets")
        ).scalar()

        # A single bulk statement instead of one INSERT per ticket in a
        # Python loop -- the loop version does one DB round-trip per
        # ticket, so resetting to a large pool (e.g. the stress test's
        # 200,000) took long enough to blow past any reasonable client
        # timeout before /reset had even returned once.
        if ticket_count > current_count:
            connection.execute(
                text("""
                    INSERT INTO tickets (ticket_number, user_id, request_id, sold)
                    SELECT gs, NULL, NULL, FALSE
                    FROM generate_series(:start, :end) AS gs
                """),
                {"start": current_count + 1, "end": ticket_count}
            )

    return {
        "message": "Sale reset successfully",
        "total_tickets": ticket_count
    }


@app.post("/buy")
def buy(request: BuyRequest):

    # ------------------------------------------------------------
    # Why the original version can double-issue a ticket:
    #
    # The old code did:
    #   1. SELECT ... WHERE request_id = :request_id   (are we new?)
    #   2. UPDATE ... SET request_id = :request_id      (claim it)
    #
    # Those are two separate statements. Under READ COMMITTED (Postgres's
    # default), two transactions running the *same* request_id can both
    # pass step 1 (both see "not found yet") before either reaches step 2,
    # because step 1 doesn't take a lock on anything — there's no row yet
    # to lock. Each transaction then goes on to grab its own ticket via
    # FOR UPDATE SKIP LOCKED (so no two transactions ever get the *same*
    # ticket number — invariant 2 holds) and commits. Result: one
    # request_id now legitimately owns two different ticket numbers,
    # which breaks invariant 3 (idempotency), even though invariants 1
    # and 2 are still fine. This is exactly the kind of bug you can only
    # catch by hammering /buy with literal duplicate request_ids
    # concurrently — a sequential test will never trigger it, which is
    # probably why the existing load_test.py run showed invariant 3 as
    # PASS: the duplicates in that script are appended to the *end* of
    # the batch, so by the time they're scheduled the originals have
    # almost always already committed.
    #
    # The fix has two parts:
    #   1. schema.sql adds UNIQUE(request_id), so the database itself
    #      cannot end up with the same request_id on two rows, no matter
    #      how the transactions interleave.
    #   2. The code below catches the resulting IntegrityError instead of
    #      assuming the UPDATE always "just works", and on conflict reads
    #      back whichever row actually won the race and returns that.
    # ------------------------------------------------------------

    with engine.begin() as connection:

        previous_purchase = connection.execute(
            text("""
                SELECT ticket_number, user_id
                FROM tickets
                WHERE request_id = :request_id
            """),
            {"request_id": request.request_id}
        ).fetchone()

        if previous_purchase:
            return {
                "message": "Request already processed",
                "ticket_number": previous_purchase.ticket_number,
                "user_id": previous_purchase.user_id,
                "request_id": request.request_id
            }

        ticket = connection.execute(
            text("""
                SELECT ticket_number
                FROM tickets
                WHERE sold = FALSE
                ORDER BY ticket_number
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """)
        ).fetchone()

        if not ticket:
            return {
                "message": "SOLD OUT",
                "request_id": request.request_id
            }

        ticket_number = ticket.ticket_number

        try:
            connection.execute(
                text("""
                    UPDATE tickets
                    SET user_id = :user_id,
                        request_id = :request_id,
                        sold = TRUE
                    WHERE ticket_number = :ticket_number
                """),
                {
                    "user_id": request.user_id,
                    "request_id": request.request_id,
                    "ticket_number": ticket_number
                }
            )
        except IntegrityError:
            # Someone else committed the same request_id in the tiny
            # window between our SELECT above and this UPDATE. Roll back
            # this transaction's failed statement, then read back
            # whichever row actually holds that request_id and return
            # it — the buyer still gets exactly one consistent answer.
            connection.rollback()
            with engine.begin() as retry_connection:
                winner = retry_connection.execute(
                    text("""
                        SELECT ticket_number, user_id
                        FROM tickets
                        WHERE request_id = :request_id
                    """),
                    {"request_id": request.request_id}
                ).fetchone()

            return {
                "message": "Request already processed",
                "ticket_number": winner.ticket_number,
                "user_id": winner.user_id,
                "request_id": request.request_id
            }

    return {
        "message": "Ticket purchased",
        "ticket_number": ticket_number,
        "user_id": request.user_id,
        "request_id": request.request_id
    }


@app.get("/status")
def status():

    with engine.connect() as connection:

        tickets = connection.execute(
            text("""
                SELECT ticket_number, user_id
                FROM tickets
                WHERE sold = TRUE
                ORDER BY ticket_number
            """)
        ).fetchall()

        tickets_sold = len(tickets)

        total_tickets = connection.execute(
            text("SELECT COUNT(*) FROM tickets")
        ).scalar()

        tickets_available = total_tickets - tickets_sold

        ticket_list = [
            {"ticket_number": t.ticket_number, "user_id": t.user_id}
            for t in tickets
        ]

    return {
        "tickets_available": tickets_available,
        "tickets_sold": tickets_sold,
        "tickets": ticket_list
    }