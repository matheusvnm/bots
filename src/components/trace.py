from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from loguru import logger
from patchright.sync_api import Page


@dataclass
class TraceContext:
    bot: str
    user_id: str
    session_id: str

    @cached_property
    def log_dir(self) -> Path:
        return Path("logs") / self.bot / self.user_id / self.session_id


class ScreenshotTracer:

    def __init__(self, ctx: TraceContext):
        self.counter = 0
        self.ctx = ctx
    
    def screenshot(self, page: Page, name: str) -> None:
        n = self.counter
        self.counter += 1
        path = self.ctx.log_dir / f"{n:03d}_{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(path))
            logger.opt(depth=1).debug("Screenshot → {}", path)
        except Exception:
            logger.opt(depth=1).error("Failed to take screenshot: {}", path)