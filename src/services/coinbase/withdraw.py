


from patchright.sync_api import Page

from components.trace import PageTracer


class CoinbaseWithdraw:
    def __init__(self, page: Page, tracer: PageTracer, **_):
        self.page = page
        self.tracer = tracer

    def run(self, **_) -> None:
        raise NotImplementedError