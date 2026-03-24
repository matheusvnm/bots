import random
from urllib.parse import parse_qs, urlparse

from loguru import logger
from patchright.sync_api import Page


def human_delay(
    page: Page,
    low: float = 1.0,
    high: float = 3.0,
) -> None:
    """Wait a random duration to mimic human pacing.

    Args:
        page: Playwright page (used for its wait_for_timeout).
        low:  Minimum delay in seconds.
        high: Maximum delay in seconds.
    """
    ms = int(random.uniform(low, high) * 1000)
    logger.debug("Human delay: {}ms", ms)
    page.wait_for_timeout(ms)


def wait_for_url(page: Page, expected_urls: list[str], timeout: int = 60000) -> bool:
    waited = 0
    while waited < timeout:
        page.wait_for_timeout(1000)
        if any(page.url.startswith(u) for u in expected_urls):
            return True
        waited += 1000
    return False


def get_query_params(url):
    """
    Parses a URL string and returns a dictionary of its query parameters.
    """
    parsed_url = urlparse(url)
    # parse_qs returns a dictionary where values are lists
    query_params = parse_qs(parsed_url.query)
    # Optional: flatten the lists to single values if you expect only one value per key
    for key, value in query_params.items():
        if len(value) == 1:
            query_params[key] = value[0]
    return query_params
