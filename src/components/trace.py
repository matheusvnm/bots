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


class PageTracer:

    def __init__(self, ctx: TraceContext):
        self.counter = 0
        self.ctx = ctx
    
    def save(self, page: Page, name: str) -> None:
        n = self.counter
        self.counter += 1
        
        screenshot_path = self.ctx.log_dir / "screenshots" / f"{n:03d}_{name}.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(screenshot_path))
            logger.opt(depth=1).debug("Screenshot → {}", screenshot_path)
        except Exception:
            logger.opt(depth=1).error("Failed to take screenshot: {}", screenshot_path)

        html_path = self.ctx.log_dir / "html" / f"{n:03d}_{name}.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = page.inner_html("body")
            html_path.write_text(content, encoding="utf-8")
            logger.opt(depth=1).debug("HTML saved → {}", html_path)
        except Exception:
            logger.opt(depth=1).error("Failed to save HTML: {}", html_path)
