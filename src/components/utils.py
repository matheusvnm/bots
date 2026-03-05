from patchright.sync_api import Page


def wait_for_url(page: Page, expected_urls: list[str], timeout: int = 60000) -> bool:
    waited = 0
    while waited < timeout:
        page.wait_for_timeout(1000)
        if any(page.url.startswith(u) for u in expected_urls):
            return True
        waited += 1000
    return False
