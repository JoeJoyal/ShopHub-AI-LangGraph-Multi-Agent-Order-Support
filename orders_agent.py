"""
# Configure the Store API URL or agent settings.
# Useful for flexible JSON-RPC parameters and responses.
# The Orders Agent calls the Store API on port 8001
# Hosts the Orders Agent, likely on port 8002.
# Handles invalid requests or downstream service errors.
# Can inspect request headers, including the Bearer authorization token.
# Validates incoming JSON-RPC request bodies.
# Adds validation rules and constraints to model fields.
"""
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

# --------------------------------------------------
# 1. Application configuration
# --------------------------------------------------

app = FastAPI(
    title="ShopHub Orders Agent",
    description=(
        "Orders Agent that exposes JSON-RPC tools "
        "and delegates order operations to the Store API."
    ),
    version="1.2.0",
)

# Store API runs separately on port 8001.
STORE_API_URL = os.getenv(
    "STORE_API_URL",
    "http://127.0.0.1:8001",
)

# --------------------------------------------------
# 2. JSON-RPC request model
# --------------------------------------------------

class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------
# 3. Define the tools available to the agent
# --------------------------------------------------

TOOLS = [
    {
        "name": "list_orders",
        "description": (
            "List orders belonging to the authenticated merchant. "
            "Optionally filter orders by status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Optional status filter, such as processing, "
                        "shipped, delivered, cancelled, or refunded."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_order",
        "description": (
            "Look up a specific order belonging to the authenticated merchant."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID, for example ORD-1001.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "cancel_order",
        "description": (
            "Cancel an order if it belongs to the authenticated merchant, "
            "the user has permission, and the order is processing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID to cancel.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for cancellation.",
                },
            },
            "required": ["order_id", "reason"],
        },
    },
    {
        "name": "refund_order",
        "description": (
            "Simulate a refund for a delivered order. "
            "The Store API checks permissions and tenant ownership."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID to refund.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the refund.",
                },
            },
            "required": ["order_id", "reason"],
        },
    },
]

# --------------------------------------------------
# 4. Health check
# --------------------------------------------------

@app.get("/", tags=["Health"])
async def home():
    return {
        "message": "ShopHub Orders Agent is running",
        "store_api_url": STORE_API_URL,
        "available_tools": len(TOOLS),
    }

# --------------------------------------------------
# 5. List available tools
# --------------------------------------------------

@app.get("/tools", tags=["Tools"])
async def list_tools():
    return {
        "tools": TOOLS,
    }

# --------------------------------------------------
# 6. Call the Store API
# --------------------------------------------------

async def call_store_api(
    method: str,
    path: str,
    authorization: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Send an authenticated request to the Store API.

    The Store API remains responsible for authentication,
    role permissions, order status validation, and tenant isolation.
    """

    headers = {
        "Authorization": authorization,
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method=method,
                url=f"{STORE_API_URL}{path}",
                headers=headers,
                params=params,
                json=body,
            )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Store API request timed out",
        )

    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to connect to the Store API. "
                "Check that port 8001 is running."
            ),
        )

    # Preserve meaningful Store API error statuses.
    if response.is_error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text or "Store API request failed"

        raise HTTPException(
            status_code=response.status_code,
            detail=detail,
        )

    try:
        return response.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Store API returned an invalid JSON response",
        )

# --------------------------------------------------
# 7. Execute a selected tool
# --------------------------------------------------

async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    authorization: str,
) -> dict[str, Any]:
    """
    Route a tool call to the corresponding Store API endpoint.
    """

    if name == "list_orders":
        status = arguments.get("status")

        params = {}
        if status:
            params["status"] = status

        return await call_store_api(
            method="GET",
            path="/orders",
            authorization=authorization,
            params=params,
        )

    elif name == "get_order":
        order_id = arguments.get("order_id")

        if not isinstance(order_id, str) or not order_id.strip():
            raise HTTPException(
                status_code=400,
                detail="A valid order_id is required",
            )

        return await call_store_api(
            method="GET",
            path="/orders/lookup",
            authorization=authorization,
            params={"id": order_id.strip()},
        )

    elif name == "cancel_order":
        order_id = arguments.get("order_id")
        reason = arguments.get("reason")

        if not isinstance(order_id, str) or not order_id.strip():
            raise HTTPException(
                status_code=400,
                detail="A valid order_id is required",
            )

        if not isinstance(reason, str) or len(reason.strip()) < 3:
            raise HTTPException(
                status_code=400,
                detail="Reason must contain at least 3 characters",
            )

        if len(reason) > 200:
            raise HTTPException(
                status_code=400,
                detail="Reason cannot exceed 200 characters",
            )

        return await call_store_api(
            method="POST",
            path="/orders/cancel",
            authorization=authorization,
            body={
                "order_id": order_id.strip(),
                "reason": reason.strip(),
            },
        )

    elif name == "refund_order":
        order_id = arguments.get("order_id")
        reason = arguments.get("reason")

        if not isinstance(order_id, str) or not order_id.strip():
            raise HTTPException(
                status_code=400,
                detail="A valid order_id is required",
            )

        if not isinstance(reason, str) or len(reason.strip()) < 3:
            raise HTTPException(
                status_code=400,
                detail="Reason must contain at least 3 characters",
            )

        if len(reason) > 200:
            raise HTTPException(
                status_code=400,
                detail="Reason cannot exceed 200 characters",
            )

        return await call_store_api(
            method="POST",
            path="/orders/refund",
            authorization=authorization,
            body={
                "order_id": order_id.strip(),
                "reason": reason.strip(),
            },
        )

    else:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown tool: {name}",
        )

# --------------------------------------------------
# 8. JSON-RPC endpoint
# --------------------------------------------------

@app.post("/rpc", tags=["JSON-RPC"])
async def rpc(
    rpc_request: JSONRPCRequest,
    request: Request,
):
    """
    Handle JSON-RPC requests such as tools/list and tools/call.
    """

    if rpc_request.jsonrpc != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": rpc_request.id,
            "error": {
                "code": -32600,
                "message": "Invalid JSON-RPC version",
            },
        }

    if rpc_request.method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rpc_request.id,
            "result": {
                "tools": TOOLS,
            },
        }

    if rpc_request.method == "tools/call":
        params = rpc_request.params

        name = params.get("name")
        arguments = params.get("arguments", {})

        if not isinstance(name, str) or not name:
            return {
                "jsonrpc": "2.0",
                "id": rpc_request.id,
                "error": {
                    "code": -32602,
                    "message": "A valid tool name is required",
                },
            }

        if not isinstance(arguments, dict):
            return {
                "jsonrpc": "2.0",
                "id": rpc_request.id,
                "error": {
                    "code": -32602,
                    "message": "Tool arguments must be an object",
                },
            }

        # Require the caller's Bearer token.
        authorization = request.headers.get("Authorization")

        if not authorization:
            return {
                "jsonrpc": "2.0",
                "id": rpc_request.id,
                "error": {
                    "code": -32001,
                    "message": "Missing Authorization header",
                },
            }

        try:
            result = await execute_tool(
                name=name,
                arguments=arguments,
                authorization=authorization,
            )

            return {
                "jsonrpc": "2.0",
                "id": rpc_request.id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": __import__("json").dumps(result),
                        }
                    ],
                    "structuredContent": result,
                    "isError": False,
                },
            }

        except HTTPException as exc:
            return {
                "jsonrpc": "2.0",
                "id": rpc_request.id,
                "error": {
                    "code": -32000,
                    "message": str(exc.detail),
                    "data": {
                        "http_status": exc.status_code,
                    },
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": rpc_request.id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {rpc_request.method}",
        },
    }