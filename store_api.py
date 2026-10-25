"""
Optional: Allows a value to optional, such as a missing phone number.
FastAPI: Creates the REST API application.
Depends: Runs a dependency, such as checking a user's token before an API request.
HTTPException: Returns HTTP errors such as 401 Unauthorized or 403 Forbidden.
HTTPBearer: Configure Bearer-token authentication in Swagger UI.
HTTPAuthorizationCredentials: Represents the received Bearer token and authentication schemem.
BaseModel: Defines and validates request and response data.
Field: Adds validation rules and defaults to model fields.
"""
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

# ======================================
# Create the FastAPI application
# ======================================
"""
Title: Name displayed in Swagger UI.
Description: Explains the API's purpose.
Version: Identifies the current API version.
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
# Configure Bearer authentication
# ======================================
security = HTTPBearer()