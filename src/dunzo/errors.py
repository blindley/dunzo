"""Error responses: a friendly HTML page for browsers, JSON for scripts and HTMX.

A request gets the HTML page when it asks for text/html (browsers navigating to a page do)
and is not an HTMX request. Everything else (the review shell's fetch calls, tests, curl) keeps
FastAPI's JSON `{"detail": ...}` shape. The status code is the same either way.

What is handled:
- HTTP errors raised by routes or the router (404, 405, 409, ...): the route's own message is
  shown when it has one, otherwise a generic one.
- Request validation errors (422): a generic message.
- A locked database (503, with Retry-After) and any other unexpected error (500). The text of an
  unexpected error is never shown to the user; the server still logs it.
"""

import sqlite3
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from dunzo.templating import templates

TITLES = {
    404: "Page not found",
    405: "Method not allowed",
    409: "Conflict",
    422: "Invalid request",
    500: "Something went wrong",
    503: "Temporarily unavailable",
}

MESSAGES = {
    404: "The page or item you asked for does not exist. It may have been deleted.",
    405: "That action is not allowed on this page.",
    409: "That change conflicts with the current state of the data.",
    422: "Some of the values in the request are not valid.",
    500: "The server hit an unexpected problem. Try again; if it keeps happening, check the server log.",
    503: "The database is busy. Try again in a moment.",
}
DEFAULT_MESSAGE = "The request could not be completed."


def wants_html(request: Request) -> bool:
    """True for a browser page load: it accepts HTML and is not an HTMX request."""
    return request.headers.get("HX-Request") != "true" and "text/html" in request.headers.get(
        "accept", ""
    )


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def error_page(
    request: Request,
    status: int,
    message: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status": status,
            "title": TITLES.get(status) or _phrase(status) or "Error",
            "message": message or MESSAGES.get(status, DEFAULT_MESSAGE),
        },
        status_code=status,
        headers=headers,
    )


def _respond(
    request: Request,
    status: int,
    detail: str,
    message: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    if wants_html(request):
        return error_page(request, status, message, headers)
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


async def _http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    if not wants_html(request):
        return await http_exception_handler(request, exc)
    detail = exc.detail
    # A bare status phrase ("Not Found") adds nothing; use the friendlier generic text instead.
    message = detail if isinstance(detail, str) and detail != _phrase(exc.status_code) else None
    return error_page(request, exc.status_code, message or None, exc.headers)


async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
    if not wants_html(request):
        return await request_validation_exception_handler(request, exc)
    return error_page(request, 422)


async def _server_error(request: Request, exc: Exception) -> Response:
    # The exception is re-raised by Starlette after this handler, so the server logs it.
    return _respond(request, 500, "Internal Server Error")


async def _database_error(request: Request, exc: sqlite3.OperationalError) -> Response:
    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        return _respond(request, 503, "The database is busy.", headers={"Retry-After": "2"})
    return await _server_error(request, exc)  # a bug (bad SQL, missing table), not load


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(sqlite3.OperationalError, _database_error)
    app.add_exception_handler(Exception, _server_error)
