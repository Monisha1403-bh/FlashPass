from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text
import os

from database import engine

SERVER_NAME = os.getenv("SERVER_NAME", "seller-default")

app = FastAPI(title="FlashPass Ticket Seller")

class BuyRequest(BaseModel):
    user_id: str
    request_id: str


@app.get("/")
def home():
    return {
        "message": "FlashPass Seller is running",
        "server": SERVER_NAME
    }

@app.post("/reset")
def reset(ticket_count: int):

    with engine.begin() as connection:

        # Reset existing tickets
        connection.execute(
            text("""
                UPDATE tickets
                SET user_id = NULL,
                    request_id = NULL,
                    sold = FALSE
            """)
        )

        # Remove tickets above requested count
        connection.execute(
            text("""
                DELETE FROM tickets
                WHERE ticket_number > :ticket_count
            """),
            {
                "ticket_count": ticket_count
            }
        )

        # Find current number of tickets
        current_count = connection.execute(
            text("""
                SELECT COUNT(*)
                FROM tickets
            """)
        ).scalar()

        # Add missing tickets
        for ticket_number in range(
            current_count + 1,
            ticket_count + 1
        ):

            connection.execute(
                text("""
                    INSERT INTO tickets
                    (
                        ticket_number,
                        user_id,
                        request_id,
                        sold
                    )
                    VALUES
                    (
                        :ticket_number,
                        NULL,
                        NULL,
                        FALSE
                    )
                """),
                {
                    "ticket_number": ticket_number
                }
            )

    return {
        "message": "Sale reset successfully",
        "total_tickets": ticket_count
    }


@app.post("/buy")
def buy(request: BuyRequest):

    with engine.begin() as connection:

        # ------------------------------------------------
        # 1. Check whether request was already processed
        # ------------------------------------------------

        previous_purchase = connection.execute(
            text("""
                SELECT ticket_number, user_id
                FROM tickets
                WHERE request_id = :request_id
            """),
            {
                "request_id": request.request_id
            }
        ).fetchone()

        if previous_purchase:

            return {
                "message": "Request already processed",
                "ticket_number": previous_purchase.ticket_number,
                "user_id": previous_purchase.user_id,
                "request_id": request.request_id
            }

        # ------------------------------------------------
        # 2. Find and lock an available ticket
        # ------------------------------------------------

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

        # No ticket available

        if not ticket:

            return {
                "message": "SOLD OUT",
                "request_id": request.request_id
            }

        ticket_number = ticket.ticket_number

        # ------------------------------------------------
        # 3. Mark ticket as sold
        # ------------------------------------------------

        connection.execute(
            text("""
                UPDATE tickets
                SET
                    user_id = :user_id,
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

    # ------------------------------------------------
    # 4. Return purchase result
    # ------------------------------------------------

    return {
        "message": "Ticket purchased",
        "ticket_number": ticket_number,
        "user_id": request.user_id,
        "request_id": request.request_id
    }


@app.get("/status")
def status():

    with engine.connect() as connection:

        # Get all sold tickets

        tickets = connection.execute(
            text("""
                SELECT ticket_number, user_id
                FROM tickets
                WHERE sold = TRUE
                ORDER BY ticket_number
            """)
        ).fetchall()

        tickets_sold = len(tickets)

        # Get total tickets

        total_tickets = connection.execute(
            text("""
                SELECT COUNT(*)
                FROM tickets
            """)
        ).scalar()

        tickets_available = (
            total_tickets - tickets_sold
        )

        # Create ticket list

        ticket_list = []

        for ticket in tickets:

            ticket_list.append({
                "ticket_number": ticket.ticket_number,
                "user_id": ticket.user_id
            })

    return {
        "tickets_available": tickets_available,
        "tickets_sold": tickets_sold,
        "tickets": ticket_list
    }