"""
@file_name: __init__.py
@author:
@date: 2026-09-22
@description: In-app browser (方案三) — public surface.

Design: reference/self_notebook/specs/2026-09-21-in-app-browser-design.md
Concrete logic lives in ``_browser_impl/`` and is not re-exported from there
directly; callers import the names listed here.
"""
from narranexus.platform.browser._browser_impl.download import (
    DEFAULT_DOWNLOAD_HOST,
    DownloadSpec,
    fetch_resumable,
    resolve_download_url,
)
from narranexus.platform.browser._browser_impl.install import (
    Downloader,
    InstallCoordinator,
    InstallOutcome,
    InstallPhase,
    InstallProgress,
)
from narranexus.platform.browser._browser_impl.locate import (
    BROWSER_HOME_ENV,
    install_root,
    locate_executable,
    probe_version,
)
from narranexus.platform.browser._browser_impl.runtime import (
    BrowserRuntimeStatus,
    RuntimeReason,
    RuntimeState,
    classify_runtime,
    detect_runtime,
)
from narranexus.platform.browser.browser_service import BrowserService

__all__ = [
    "BROWSER_HOME_ENV",
    "DEFAULT_DOWNLOAD_HOST",
    "BrowserRuntimeStatus",
    "BrowserService",
    "DownloadSpec",
    "Downloader",
    "InstallCoordinator",
    "InstallOutcome",
    "InstallPhase",
    "InstallProgress",
    "RuntimeReason",
    "RuntimeState",
    "classify_runtime",
    "detect_runtime",
    "fetch_resumable",
    "install_root",
    "locate_executable",
    "probe_version",
    "resolve_download_url",
]
