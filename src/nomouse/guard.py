"""Request guard that keeps a walk on one origin and free of side effects."""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Request, Route

BlockReason = Literal["non-GET", "off-origin", "off-page navigation"]

_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class BlockedRequest:
    """A request the guard aborted.

    Attributes:
        method: HTTP method of the request.
        url: Requested URL.
        reason: Which guard rule aborted it.
    """

    method: str
    url: str
    reason: BlockReason


class RequestGuard:
    """Aborts every non-GET request and every request to another origin.

    Once pinned, it also aborts top-level navigations away from the pinned
    document, so pressing Enter on a submit button cannot leave the walked
    page. Requests are aborted with `net::ERR_ABORTED`, which leaves the
    current document in place instead of committing an error page.

    Attributes:
        blocked: Requests aborted so far, in order.
    """

    def __init__(self, allowed_url: str) -> None:
        """Initializes the guard.

        Args:
            allowed_url: Any URL on the one origin that requests may reach.
        """
        self._origin = _origin(allowed_url)
        self._pinned: str | None = None
        self.blocked: list[BlockedRequest] = []

    def install(self, context: BrowserContext) -> None:
        """Routes every request of a browser context through the guard.

        The context should block service workers, because requests a service
        worker answers bypass Playwright routes.

        Args:
            context: The browser context to guard.
        """
        context.route("**/*", self._handle)

    def pin(self, url: str) -> None:
        """Aborts later top-level navigations to any other document.

        Args:
            url: The document to stay on; its fragment is ignored.
        """
        self._pinned = _without_fragment(url)

    def _handle(self, route: Route, request: Request) -> None:
        reason = self._block_reason(request)
        if reason is None:
            route.continue_()
            return
        self.blocked.append(
            BlockedRequest(method=request.method, url=request.url, reason=reason)
        )
        route.abort("aborted")

    def _block_reason(self, request: Request) -> BlockReason | None:
        if request.method != "GET":
            return "non-GET"
        if _origin(request.url) != self._origin:
            return "off-origin"
        if (
            self._pinned is not None
            and request.is_navigation_request()
            and request.frame.parent_frame is None
            and _without_fragment(request.url) != self._pinned
        ):
            return "off-page navigation"
        return None


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return (
        scheme,
        (parts.hostname or "").lower(),
        parts.port or _DEFAULT_PORTS.get(scheme),
    )


def _without_fragment(url: str) -> str:
    return urlsplit(url)._replace(fragment="").geturl()
