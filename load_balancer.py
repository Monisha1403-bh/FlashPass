from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.openapi.utils import get_openapi
import requests
import itertools


app = FastAPI(
    title="FlashPass Ticket System",
    description="Ticket booking service with load balancing",
    version="1.0.0"
)


SELLERS = [
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8002",
    "http://127.0.0.1:8003"
]


seller_cycle = itertools.cycle(SELLERS)


class BuyRequest(BaseModel):
    user_id: str
    request_id: str


@app.get(
    "/",
    summary="Check service status"
)
def home():

    return {
        "message": "FlashPass Ticket System is running"
    }


@app.post(
    "/reset",
    summary="Reset ticket inventory"
)
def reset(ticket_count: int):

    response = requests.post(
        f"{SELLERS[0]}/reset",
        params={
            "ticket_count": ticket_count
        },
        timeout=30
    )

    return response.json()


@app.post(
    "/buy",
    summary="Purchase a ticket"
)
def buy(request: BuyRequest):

    seller = next(seller_cycle)

    response = requests.post(
        f"{seller}/buy",
        json={
            "user_id": request.user_id,
            "request_id": request.request_id
        },
        timeout=30
    )

    return response.json()


@app.get(
    "/status",
    summary="View ticket status"
)
def status():

    seller = SELLERS[0]

    response = requests.get(
        f"{seller}/status",
        timeout=30
    )

    return response.json()


# ---------------------------------------------------------
# CLEAN SWAGGER / OPENAPI DOCUMENTATION
# ---------------------------------------------------------

def custom_openapi():

    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="FlashPass Ticket System",
        version="1.0.0",
        description=(
            "FlashPass ticket booking API.\n\n"
            "Use the endpoints below to reset the ticket inventory, "
            "purchase tickets, and check ticket status."
        ),
        routes=app.routes
    )

    # Remove automatically generated 422 validation
    # responses from Swagger documentation.

    for path in openapi_schema["paths"].values():

        for operation in path.values():

            if isinstance(operation, dict):

                responses = operation.get("responses", {})

                responses.pop("422", None)

    # Remove validation error schemas because we removed
    # their documentation above.

    schemas = openapi_schema.get(
        "components",
        {}
    ).get(
        "schemas",
        {}
    )

    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)

    app.openapi_schema = openapi_schema

    return app.openapi_schema


app.openapi = custom_openapi