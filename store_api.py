"""
# Optional: Allows a value to optional, such as a missing phone number.
# FastAPI: Creates the REST API application.
# Depends: Runs a dependency, such as checking a user's token before an API request.
# HTTPException: Returns HTTP errors such as 401 Unauthorized or 403 Forbidden.
# HTTPBearer: Configure Bearer-token authentication in Swagger UI.
# HTTPAuthorizationCredentials: Represents the received Bearer token and authentication schemem.
# BaseModel: Defines and validates request and response data.
# Field: Adds validation rules and defaults to model fields.
"""
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

# ======================================
# Create the FastAPI application
# ======================================
"""
# Title: Name displayed in Swagger UI.
# Description: Explains the API's purpose.
# Version: Identifies the current API version.
"""
app = FastAPI(
    title="ShopHub Mock Store API",
    description=(
        "Mock order store with tenant isolation "
        "and role-based access control (RBAC)."
    ),
    version="1.2.0",
)

# ======================================
# 1. Configure Bearer authentication
# ======================================
security = HTTPBearer()

# =======================================
# 2. Demo users and authentication tokens
# =======================================
"""
# Merchant A - Admin: Can manage merchant operations.
# Merchant A - Support Agent: Handles customer order inquiries and may cancel or refund orders if granted those permissions.
# Merchant A - Viewer: Typically reads order information but cannot modify orders.
# Merchant B - Order Manager
# user_id: identifies the individual user.
# merchant_id: identifies the merchant or tenant the user belongs to.
# role: determines the user's access permissions.
"""
TOKENS = {
    "token-a-admin": {
        "user_id": "user-001",
        "merchant_id": "merchant-a",
        "role": "merchant_admin",
    },
    "token-a-support": {
        "user_id": "user-002",
        "merchant_id": "merchant-a",
        "role": "support_agent",
    },
    "token-a-viewer": {
        "user_id": "user-003",
        "merchant_id": "merchant-a",
        "role": "viewer",
    },
    "token-b-manager": {
        "user_id": "user-004",
        "merchant_id": "merchant-b",
        "role": "order_manager",
    },
}

# =======================================
# 3. Define permissions for each role
# =======================================
ROLE_PERMISSIONS = {
    "merchant_admin": {
        "orders:read",
        "orders:cancel",
        "orders:refund",
        "staff:manage",
    },
    "order_manager": {
        "orders:read",
        "orders:cancel",
        "orders:refund",
    },
    "support_agent": {
        "orders:read",
        "orders:cancel",
    },
    "viewer": {
        "orders:read",
    },
}

# =======================================
# 4. Create mock order data
# =======================================
"""
Data is stored in memory and resets when the API restarts.
"""

ORDERS = {}

for merchant_id, prefix in [
    ("merchant-a", "ORD-10"),
    ("merchant-b", "ORD-20"),
]:
    for number in range(1, 7):
        order_id = f"{prefix}{number:02d}"

        ORDERS[order_id] = {
            "order_id": order_id,
            "merchant_id": merchant_id,
            "customer": f"Customer {number}",
            "status": "shipped" if number == 1 else "processing",
            "amount": 1000 + number * 100,
        }


# A delivered order for testing the refund workflow.
ORDERS["ORD-1007"] = {
    "order_id": "ORD-1007",
    "merchant_id": "merchant-a",
    "customer": "Customer 7",
    "status": "delivered",
    "amount": 1700,
}

# =======================================
# 5. Define request model for order actions
# =======================================
"""
# OrderAction: the name of your request model.
# BaseModel: comes from Pydantic and validates incoming request data.
# FastAPI: can use this model to validate JSON request bodies and generate the corresponding Swagger UI schema.
"""
class OrderAction(BaseModel):
    order_id: str = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=200)

# --------------------------------------------------
# 6. Authenticate the user
# --------------------------------------------------
def get_current_merchant(authorization: Optional[str]) -> dict:
    """
    # Validate the demo Bearer token and return the caller identity.
    # get_current_merchant is the function name.
    # authorization: Optional[str] means the input can be a string or None.
    # -> dict indicates that the function returns a dictionary containing the user's identity.
    # Check whether the authorization header exists
    # Split the authentication scheme and token e.g: Bearer token-a-support
    # Validate the format: Bearer token-a-support → correct format.
    # Look up the token is token-a-support, the lookup returns the corresponding identity.
    # Reject invalid tokens -> Authorization: Bearer fake-token-123
    # Return a copy of the identify
    """

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token",
        )

    scheme, separator, token = authorization.partition(" ")

    if (
        scheme.lower() != "bearer"
        or not separator
        or not token.strip()
    ):
        raise HTTPException(
            status_code=401,
            detail="Use Authorization: Bearer <token>",
        )

    identity = TOKENS.get(token.strip())

    if identity is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer token",
        )

    # Return a copy so callers cannot modify the token dictionary.
    return identity.copy()

# ====================================================================
# Extract the Bearer token supplied through Swagger or an API client.
# ====================================================================
def get_authenticated_merchant(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    # Bridge between the incoming HTTP request and your token-validation function.
    # Depends(security) tells FastAPI to run the HTTPBearer() dependency you created earlier.
    # HTTPBearer reads the Authorization header.
    # HTTPAuthorizationCredentials contains the authentication scheme and token.
    # Extract the token
    """

    token = credentials.credentials

    return get_current_merchant(f"Bearer {token}")

# ===================================
# 7. Role-Based Access Control (RBAC)
# ===================================
def require_permission(identity: dict, permission: str) -> None:
    """
    # Reject the request if the user's role lacks the permission.
    # Accept the user identify and required permission
     - identity: the authenticated user's details.
     - permission: the action the user wants to perform.
    # Get the user's role
    # Check the role's permissions
     - ROLE_PERMISSIONS.get(role, set()) retrieves the permissions for that role.
     - If the role is unknown, it returns an empty set.
     - permission not in ... checks whether the requested permission is missing.
    # Reject unauthorized requests
    """

    role = identity.get("role")

    if permission not in ROLE_PERMISSIONS.get(role, set()):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Role '{role}' does not have "
                f"permission '{permission}'"
            ),
        )


# --------------------------------------------------
# 8. Find an order with tenant isolation
# --------------------------------------------------

def find_order(order_id: str, merchant_id: str) -> dict:
    """
    # Return an order only if it belongs to the authenticated merchant.
    # Accept the order ID and merchant ID
     - order_id: the order the user wants to access.
     - merchant_id: the merchant ID associated with the authenticated user.
    # Check whether the order belongs to the merchant. This condition checks two possibilities:
     - The order does not exist.
     - The order exists, but belongs to a different merchant.
    # Return a 404 error
    # Return the order if access is allowed
    """

    order = ORDERS.get(order_id)

    # Return the same error for missing and foreign-merchant orders.
    if order is None or order["merchant_id"] != merchant_id:
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )

    return order


# --------------------------------------------------
# 9. Health check
# --------------------------------------------------

@app.get("/", tags=["Health"])
def home():
    return {
        "message": "ShopHub Store API is running",
        "docs": "/docs",
    }

# --------------------------------------------------
# 10. Get the authenticated user's identity
# --------------------------------------------------

@app.get("/auth/me", tags=["Authentication"])
def auth_me(
    identity: dict = Depends(get_authenticated_merchant),
):
    return {
        "user_id": identity["user_id"],
        "merchant_id": identity["merchant_id"],
        "role": identity["role"],
        "permissions": sorted(
            ROLE_PERMISSIONS.get(identity["role"], set())
        ),
    }

# --------------------------------------------------
# 11. List orders belonging to the current merchant
# --------------------------------------------------

@app.get("/orders", tags=["Orders"])
def list_orders(
    status: Optional[str] = None,
    identity: dict = Depends(get_authenticated_merchant),
):
    require_permission(identity, "orders:read")

    merchant_id = identity["merchant_id"]

    results = [
        order.copy()
        for order in ORDERS.values()
        if order["merchant_id"] == merchant_id
        and (status is None or order["status"] == status)
    ]

    return {
        "merchant_id": merchant_id,
        "count": len(results),
        "orders": results,
    }

# --------------------------------------------------
# 12. Look up a single order
# --------------------------------------------------

@app.get("/orders/lookup", tags=["Orders"])
def lookup_order(
    id: str,
    identity: dict = Depends(get_authenticated_merchant),
):
    require_permission(identity, "orders:read")

    order = find_order(
        order_id=id,
        merchant_id=identity["merchant_id"],
    )

    return {
        "order": order.copy(),
    }


# --------------------------------------------------
# 13. Cancel an order
# --------------------------------------------------

@app.post("/orders/cancel", tags=["Order Actions"])
def cancel_order(
    action: OrderAction,
    identity: dict = Depends(get_authenticated_merchant),
):
    require_permission(identity, "orders:cancel")

    order = find_order(
        order_id=action.order_id,
        merchant_id=identity["merchant_id"],
    )

    # Only processing orders can be cancelled.
    if order["status"] != "processing":
        raise HTTPException(
            status_code=409,
            detail="Only processing orders can be cancelled",
        )

    order["status"] = "cancelled"

    return {
        "message": "Order cancelled in demo store",
        "order_id": order["order_id"],
        "status": order["status"],
        "reason": action.reason,
    }

# --------------------------------------------------
# 14. Simulate a refund
# --------------------------------------------------

@app.post("/orders/refund", tags=["Order Actions"])
def refund_order(
    action: OrderAction,
    identity: dict = Depends(get_authenticated_merchant),
):
    require_permission(identity, "orders:refund")

    order = find_order(
        order_id=action.order_id,
        merchant_id=identity["merchant_id"],
    )

    if order["status"] == "refunded":
        raise HTTPException(
            status_code=409,
            detail="Order is already refunded",
        )

    # Only delivered orders can be refunded.
    if order["status"] != "delivered":
        raise HTTPException(
            status_code=409,
            detail="Only delivered orders can be refunded",
        )

    # This changes mock order data only; it does not move real money.
    order["status"] = "refunded"

    return {
        "message": (
            "Refund simulated successfully; "
            "no real payment was made"
        ),
        "order_id": order["order_id"],
        "status": order["status"],
        "reason": action.reason,
    }