"""HTTP details shared by the authorization and token endpoints: form posts, errors, no-store."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def is_form_post(request: Request) -> bool:
    content_type = request.headers.get("content-type", "").split(";")[0]

    return content_type == "application/x-www-form-urlencoded"


def oauth_error(code: str) -> JSONResponse:
    return JSONResponse(
        content={"error": code},
        status_code=400,
        headers=NO_STORE,
    )
