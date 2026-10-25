import os
import re

import httpx
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

# --------------------------------------------------
# 1. Application configuration
# --------------------------------------------------

app = FastAPI(
    title="ShopHub Main Orchestrator",
    description=(
        "Receives merchant requests, identifies the intended order operation, "
        "and routes it to the Orders Agent."
    ),
    version="1.2.0",
)

STORE_API_URL = os.getenv(
    "STORE_API_URL",
    "http://127.0.0.1:8001",
)

ORDERS_AGENT_URL = os.getenv(
    "ORDERS_AGENT_URL",
    "http://127.0.0.1:8002",
)

# Displays the Authorize button in Swagger UI.
security = HTTPBearer()


# --------------------------------------------------
# 2. Request model
# --------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=1000,
        description="The merchant's request in plain English.",
    )

# --------------------------------------------------
# 3. Health check
# --------------------------------------------------

@app.get("/", tags=["Health"])
async def home():
    return {
        "message": "ShopHub Main Orchestrator is running",
        "store_api_url": STORE_API_URL,
        "orders_agent_url": ORDERS_AGENT_URL,
    }

# --------------------------------------------------
# 4. Authenticate the user through the Store API
# --------------------------------------------------

async def verify_user(authorization: str) -> dict:
    """
    Ask the Store API to validate the caller's Bearer token.

    The Store API is the source of truth for user identity.
    """

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{STORE_API_URL}/auth/me",
                headers={"Authorization": authorization},
            )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Store API authentication request timed out",
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="Cannot connect to the Store API on port 8001",
        )

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=response.status_code,
            detail="Authentication failed. Check your Bearer token.",
        )

    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="The Store API could not verify the user",
        )

    return response.json()

# --------------------------------------------------
# 5. Identify the intended operation
# --------------------------------------------------

def classify_request(message: str) -> dict:
    """
    Use simple keyword rules to identify the requested operation.

    This is a rule-based classifier, not an LLM.
    """

    text = message.strip().lower()

    # Extract an order ID such as ORD-1001.
    match = re.search(r"\bord-\d+\b", text, re.IGNORECASE)
    order_id = match.group(0).upper() if match else None

    # Refund requests must be identified before general order lookups.
    if re.search(r"\b(refund|reimburse)\b", text):
        if not order_id:
            raise HTTPException(
                status_code=400,
                detail="Please include an order ID, such as ORD-1007.",
            )

        reason_match = re.search(
            r"\b(?:because|reason(?:\s+is)?|due\s+to)\s+(.+)",
            message.strip(),
            re.IGNORECASE,
        )
        reason = (
            reason_match.group(1).strip(" .")
            if reason_match
            else "Customer requested a refund"
        )

        return {
            "intent": "refund_order",
            "arguments": {
                "order_id": order_id,
                "reason": reason[:200],
            },
        }

    # Cancellation requests.
    if re.search(r"\b(cancel|cancellation)\b", text):
        if not order_id:
            raise HTTPException(
                status_code=400,
                detail="Please include an order ID, such as ORD-1002.",
            )

        reason_match = re.search(
            r"\b(?:because|reason(?:\s+is)?|due\s+to)\s+(.+)",
            message.strip(),
            re.IGNORECASE,
        )
        reason = (
            reason_match.group(1).strip(" .")
            if reason_match
            else "Customer requested cancellation"
        )

        return {
            "intent": "cancel_order",
            "arguments": {
                "order_id": order_id,
                "reason": reason[:200],
            },
        }

    # Look up one specific order.
    if order_id:
        return {
            "intent": "get_order",
            "arguments": {
                "order_id": order_id,
            },
        }

    # List orders, optionally filtering by status.
    if re.search(
        r"\b(list|show|display|all|find)\b.*\b(orders|order)\b"
        r"|\b(orders|order)\b.*\b(list|show|display)\b",
        text,
    ):
        status = None

        for candidate in (
            "processing",
            "shipped",
            "delivered",
            "cancelled",
            "refunded",
        ):
            if re.search(rf"\b{candidate}\b", text):
                status = candidate
                break

        return {
            "intent": "list_orders",
            "arguments": {"status": status} if status else {},
        }

    # Handle a few common requests without requiring the word "list".
    if re.search(r"\b(my|merchant)\s+orders\b", text):
        return {
            "intent": "list_orders",
            "arguments": {},
        }

    raise HTTPException(
        status_code=400,
        detail=(
            "I could not identify the requested operation. Try asking to "
            "list orders, look up an order, cancel an order, or refund an order."
        ),
    )

# --------------------------------------------------
# 6. Call the Orders Agent through JSON-RPC
# --------------------------------------------------

async def call_orders_agent(
    tool_name: str,
    arguments: dict,
    authorization: str,
) -> dict:
    """
    Send the selected tool and its arguments to the Orders Agent.
    Forward the caller's token so the Store API can authorize the action.
    """

    rpc_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{ORDERS_AGENT_URL}/rpc",
                json=rpc_payload,
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                },
            )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Orders Agent request timed out",
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail="Cannot connect to the Orders Agent on port 8002",
        )

    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail="The Orders Agent returned an HTTP error",
        )

    try:
        rpc_response = response.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="The Orders Agent returned invalid JSON",
        )

    # The Orders Agent reports tool errors in the JSON-RPC error field.
    if "error" in rpc_response:
        error = rpc_response["error"]
        error_data = error.get("data") or {}
        http_status = error_data.get("http_status", 502)

        # Only propagate recognized HTTP error codes.
        if not isinstance(http_status, int) or not 400 <= http_status <= 599:
            http_status = 502

        raise HTTPException(
            status_code=http_status,
            detail=error.get("message", "Orders Agent tool call failed"),
        )

    result = rpc_response.get("result")

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=502,
            detail="Unexpected JSON-RPC response from Orders Agent",
        )

    # Use structuredContent returned by our Orders Agent.
    if "structuredContent" in result:
        return result["structuredContent"]

    # Fallback for a tool response containing text content.
    return result

# --------------------------------------------------
# 7. Main chat endpoint
# --------------------------------------------------

@app.post("/chat", tags=["Orchestrator"])
async def chat(
    request: ChatRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Process a merchant's plain-English order request.

    Flow:
    1. Extract the Bearer token.
    2. Verify the user with the Store API.
    3. Classify the request.
    4. Call the corresponding Orders Agent tool.
    5. Return the result.
    """

    authorization = f"Bearer {credentials.credentials}"

    # Step 1: Verify the user's identity.
    identity = await verify_user(authorization)

    # Step 2: Determine the requested operation.
    routed_request = classify_request(request.message)

    tool_name = routed_request["intent"]
    arguments = routed_request["arguments"]

    # Step 3: Execute the operation through the Orders Agent.
    result = await call_orders_agent(
        tool_name=tool_name,
        arguments=arguments,
        authorization=authorization,
    )

    # Step 4: Return a structured response to the client.
    return {
        "message": "Request processed successfully",
        "user": {
            "user_id": identity["user_id"],
            "merchant_id": identity["merchant_id"],
            "role": identity["role"],
        },
        "intent": tool_name,
        "arguments": arguments,
        "result": result,
    }
