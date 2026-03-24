from loguru import logger
from patchright.sync_api import Page

from components.trace import PageTracer
from services.kraken.browser.interceptors import KrakenInterceptor


class KrakenWithdraw:
    def __init__(
        self, page: Page, tracer: PageTracer, interceptor: KrakenInterceptor, **_
    ):
        self.page = page
        self.tracer = tracer
        self.interceptor = interceptor

    def run(self) -> None:
        raise NotImplementedError
