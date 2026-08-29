"""Browser control: open a browser, navigate to a URL, or jump to a settings page."""

from __future__ import annotations

import subprocess
import webbrowser
from collections.abc import Sequence
from typing import Any

from ..platform_win import is_windows, which
from .base import ActionDef, ActionResult, ExecContext, Param, RiskLevel, Skill

BROWSERS: dict[str, str] = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "default": "",
}

# Shortcuts for pages people ask for by name rather than URL.
KNOWN_PAGES: dict[str, str] = {
    "chrome settings": "chrome://settings",
    "chrome extensions": "chrome://extensions",
    "chrome history": "chrome://history",
    "chrome downloads": "chrome://downloads",
    "edge settings": "edge://settings",
    "firefox settings": "about:preferences",
}


# Internal pages every Chromium-family browser exposes under its own scheme.
INTERNAL_PAGES = ("settings", "extensions", "history", "downloads", "bookmarks")


def normalize_url(value: str, browser: str = "default") -> str:
    """Turn what a person says into something a browser will accept."""
    target = value.strip()
    lowered = target.lower()
    if lowered in KNOWN_PAGES:
        return KNOWN_PAGES[lowered]
    if lowered in INTERNAL_PAGES and browser in ("chrome", "edge"):
        # "open chrome and go to settings" means chrome://settings.
        return f"{browser}://{lowered}"
    if "://" in target or target.startswith("about:"):
        return target
    if " " in target or "." not in target:
        from urllib.parse import quote_plus

        return f"https://www.google.com/search?q={quote_plus(target)}"
    return f"https://{target}"


class BrowserSkill(Skill):
    name = "browser"
    description = "Open web pages and browser settings."

    def actions(self) -> Sequence[ActionDef]:
        return (
            ActionDef(
                name="browser_open",
                skill=self.name,
                description=(
                    "Open a URL, a search, or a browser page such as 'chrome settings'."
                ),
                risk=RiskLevel.REVERSIBLE,
                handler=self.open_url,
                params=(
                    Param("target", description="URL, search text, or named browser page."),
                    Param("browser", required=False, default="default",
                          enum=tuple(BROWSERS), description="Which browser to use."),
                ),
                summarize=lambda a: (
                    f"Open {a['target']} in {a.get('browser', 'default')} browser"
                ),
            ),
        )

    def open_url(self, args: dict[str, Any], ctx: ExecContext) -> ActionResult:
        browser = (args.get("browser") or "default").lower()
        url = normalize_url(args["target"], browser)
        if ctx.dry_run:
            return ActionResult(True, f"Would open {url} in the {browser} browser.",
                                {"dry_run": True, "url": url})
        if browser == "default" or browser not in BROWSERS:
            opened = webbrowser.open(url)
            if not opened:
                return ActionResult(False, f"No default browser available to open {url}.")
            return ActionResult(True, f"Opened {url}.", {"url": url})

        executable = BROWSERS[browser]
        if not is_windows():
            executable = which(executable.removesuffix(".exe")) or ""
            if not executable:
                webbrowser.open(url)
                return ActionResult(True, f"Opened {url} in the default browser.", {"url": url})
        try:
            subprocess.Popen([executable, url])
        except OSError as exc:
            return ActionResult(False, f"Could not open {browser}: {exc}")
        return ActionResult(True, f"Opened {url} in {browser}.", {"url": url})
