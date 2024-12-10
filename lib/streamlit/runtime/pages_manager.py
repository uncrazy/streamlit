# Copyright (c) Streamlit Inc. (2018-2022) Snowflake Inc. (2022-2025)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Final

from streamlit import source_util
from streamlit.errors import StreamlitAPIException
from streamlit.logger import get_logger
from streamlit.util import calc_md5

if TYPE_CHECKING:
    from streamlit.runtime.scriptrunner.script_cache import ScriptCache
    from streamlit.source_util import PageHash, PageInfo, PageName, ScriptPath

_LOGGER: Final = get_logger(__name__)


class PagesManager:
    """
    PagesManager is responsible for managing the set of pages based on the
    strategy. By default, PagesManager uses V1 which relies on the original
    assumption that there exists a `pages` directory with all the scripts.

    If the `pages` are being set directly, the strategy is switched to V2.
    This indicates someone has written an `st.navigation` call in their app
    which informs us of the pages.

    NOTE: Each strategy handles its own thread safety when accessing the pages
    """

    def __init__(
        self,
        main_script_path: ScriptPath,
        script_cache: ScriptCache | None = None,
        **kwargs,
    ):
        self._main_script_path = main_script_path
        self._main_script_hash: PageHash = calc_md5(main_script_path)
        self._script_cache = script_cache
        self._intended_page_script_hash: PageHash | None = None
        self._intended_page_name: PageName | None = None
        self._current_page_script_hash: PageHash = ""
        self._pages: dict[PageHash, PageInfo] = None

        has_pages_folder = os.path.exists(self.main_script_parent / "pages")
        if has_pages_folder:
            self._pages = source_util.get_pages(self._main_script_path)

        # Save the flag for future calls.
        self._has_pages_folder = has_pages_folder

    @property
    def main_script_path(self) -> ScriptPath:
        return self._main_script_path

    @property
    def main_script_parent(self) -> Path:
        return Path(self._main_script_path).parent

    @property
    def main_script_hash(self) -> PageHash:
        return self._main_script_hash

    @property
    def current_page_script_hash(self) -> PageHash:
        return self._current_page_script_hash

    @property
    def intended_page_name(self) -> PageName | None:
        return self._intended_page_name

    @property
    def intended_page_script_hash(self) -> PageHash | None:
        return self._intended_page_script_hash

    @property
    def initial_active_script_hash(self) -> PageHash:
        return self.main_script_hash

    @property
    def mpa_version(self) -> int:
        return 1 if self._has_pages_folder else 2

    def set_current_page_script_hash(self, page_script_hash: PageHash) -> None:
        self._current_page_script_hash = page_script_hash

    def get_main_page(self) -> PageInfo:
        return {
            "script_path": self._main_script_path,
            "page_script_hash": self._main_script_hash,
        }

    def set_script_intent(
        self, page_script_hash: PageHash, page_name: PageName
    ) -> None:
        self._intended_page_script_hash = page_script_hash
        self._intended_page_name = page_name

    def get_initial_active_script(
        self, page_script_hash: PageHash, page_name: PageName
    ) -> PageInfo | None:
        return {
            # We always run the main script in V2 as it's the common code
            "script_path": self._main_script_path,
            "page_script_hash": page_script_hash
            or self._main_script_hash,  # Default Hash
        }

    def get_pages(self) -> dict[PageHash, PageInfo]:
        # If pages are not set, provide the common page info where
        # - the main script path is the executing script to start
        # - the page script hash and name reflects the intended page requested
        return self._pages or {
            self.main_script_hash: {
                "page_script_hash": self.intended_page_script_hash or "",
                "page_name": self.intended_page_name or "",
                "icon": "",
                "script_path": self.main_script_path,
            }
        }

    def set_pages(self, pages: dict[PageHash, PageInfo]) -> None:
        if self._has_pages_folder:
            raise StreamlitAPIException(
                "We've detected a multi page app using the pages folder calling st.navigation. Please rename the folder to enable our updated multipage apps."
            )

        self._pages = pages

    def get_page_script(self, fallback_page_hash: PageHash = "") -> PageInfo | None:
        if self._pages is None:
            return None

        if self.intended_page_script_hash:
            # We assume that if initial page hash is specified, that a page should
            # exist, so we check out the page script hash or the default page hash
            # as a backup
            return self._pages.get(
                self.intended_page_script_hash,
                self._pages.get(fallback_page_hash, None),
            )
        elif self.intended_page_name:
            # If a user navigates directly to a non-main page of an app, the
            # the page name can identify the page script to run
            return next(
                filter(
                    # There seems to be this weird bug with mypy where it
                    # thinks that p can be None (which is impossible given the
                    # types of pages), so we add `p and` at the beginning of
                    # the predicate to circumvent this.
                    lambda p: p and (p["url_pathname"] == self.intended_page_name),
                    self._pages.values(),
                ),
                None,
            )

        return self._pages.get(fallback_page_hash, None)

    def register_pages_changed_callback(
        self,
        callback: Callable[[str], None],
    ) -> Callable[[], None]:
        """Register a callback to be called when the set of pages changes.

        The callback will be called with the path changed.
        """
        if self._has_pages_folder:
            return source_util.register_pages_changed_callback(callback)

        return lambda: None

    def get_page_script_byte_code(self, script_path: str) -> Any:
        if self._script_cache is None:
            # Returning an empty string for an empty script
            return ""

        return self._script_cache.get_bytecode(script_path)

    def get_page_script_path(self, page_script_hash: PageHash) -> PageHash:
        if self._pages is None or page_script_hash not in self._pages:
            return self.main_script_path

        return self._pages[page_script_hash].get("script_path", "")

    def was_page_found(self, page_script_hash: PageHash, page_name: PageName) -> bool:
        return self._pages and (
            page_script_hash in self._pages
            or any(p["url_pathname"] == page_name for p in self._pages.values())
        )
