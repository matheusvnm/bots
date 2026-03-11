"""
Network debug logger component.

Attaches request/response/requestfailed listeners to a Playwright page.
All traffic is emitted via loguru with network_debug=True so it is routed
exclusively to logs/network_debug.log (filtered in logger.py sinks).
"""

from loguru import logger
from patchright.sync_api import Page, Response

_nlog = logger.bind(network_debug=True)


def attach_network_logger(page: Page) -> None:
    """Attach network debug logging to *page*."""

    def on_request(request) -> None:
        try:
            data = request.post_data
        except Exception:
            data = "<binary or unavailable>"
        try:
            _nlog.debug("[REQ]  {} {}  data={}", request.method, request.url, data)
        except Exception:
            pass

    def on_response(response: Response) -> None:
        req = response.request
        try:
            data = response.text()
        except Exception:
            data = "<binary or unavailable>"
        try:
            _nlog.debug(
                "[RES]  {} {}  status={}  data={}",
                req.method,
                req.url,
                response.status,
                data,
            )
        except Exception:
            pass

    def on_request_failed(request) -> None:
        try:
            data = request.post_data
        except Exception:
            data = "<binary or unavailable>"
        try:
            _nlog.debug("[REQ_ERR]  {} {}  data={}", request.method, request.url, data)
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
