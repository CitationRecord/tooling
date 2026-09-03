"""Rendered page capture via Playwright (Chromium).

One browser per run, one fresh context per claim so no cookie or storage
state carries between vendors.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from . import USER_AGENT
from .config import Config


class FetchError(RuntimeError):
    """The page could not be captured."""


@dataclass(frozen=True)
class PageCapture:
    html: str
    screenshot_png: bytes
    text: str
    final_url: str
    http_status: int | None
    content_type: str | None
    title: str | None
    screenshot_full_page: bool


class Browser:
    """Thin wrapper over a Playwright Chromium instance."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._playwright = None
        self._browser = None

    @property
    def version(self) -> str | None:
        if self._browser is None:
            return None
        return f"chromium/{self._browser.version}"

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - install-time failure
            raise FetchError(
                "playwright is not installed. Run:\n"
                "  pip install -r requirements.txt\n"
                "  py -m playwright install chromium"
            ) from exc

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=self._config.headless)
        except Exception as exc:
            self.stop()
            raise FetchError(
                f"could not launch Chromium ({exc}). "
                "Run: py -m playwright install chromium"
            ) from exc

    def stop(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            finally:
                self._playwright = None

    def capture(self, url: str, wait_until: str, settle_ms: int, full_page: bool) -> PageCapture:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        if self._browser is None:
            raise FetchError("browser not started")

        config = self._config
        context = self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": config.viewport_width, "height": config.viewport_height},
            locale="en-US",
            timezone_id="UTC",
        )
        context.set_default_timeout(config.page_timeout_ms)
        page = context.new_page()
        try:
            try:
                response = page.goto(
                    url, wait_until=wait_until, timeout=config.page_timeout_ms
                )
            except PlaywrightTimeout:
                # networkidle never settles on pages with long-poll or
                # analytics beacons. Fall back to whatever has rendered.
                response = None
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=config.page_timeout_ms)
                except PlaywrightTimeout as exc:
                    raise FetchError(f"timed out loading {url}") from exc

            if settle_ms:
                page.wait_for_timeout(settle_ms)

            html = page.content()
            title = page.title()
            try:
                text = page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )
            except PlaywrightError:
                text = ""

            screenshot_full_page = full_page
            try:
                screenshot = page.screenshot(full_page=full_page)
            except PlaywrightError:
                # Very tall pages exceed the capture surface limit.
                screenshot = page.screenshot(full_page=False)
                screenshot_full_page = False

            content_type = None
            http_status = None
            if response is not None:
                http_status = response.status
                content_type = response.header_value("content-type")

            return PageCapture(
                html=html,
                screenshot_png=screenshot,
                text=text or "",
                final_url=page.url,
                http_status=http_status,
                content_type=content_type,
                title=title or None,
                screenshot_full_page=screenshot_full_page,
            )
        except FetchError:
            raise
        except PlaywrightError as exc:
            raise FetchError(f"{exc.__class__.__name__}: {str(exc).splitlines()[0]}") from exc
        finally:
            context.close()


@contextmanager
def browser(config: Config) -> Iterator[Browser]:
    instance = Browser(config)
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()
