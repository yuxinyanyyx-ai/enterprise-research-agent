

   
from __future__ import annotations

import os
from typing import Any
import httpx
from dotenv import load_dotenv

load_dotenv()

"""Cortellis 数据访问客户端。

    这里只负责：
    1. 发送 HTTP 请求
    2. 获取原始 JSON
    3. 处理分页和基础异常

    不在这里做业务分析、实体对齐和 LLM 调用。
    """
class CortellisClient:

    BASE_URL = "https://www.cortellis.com/intelligence"
    SEARCH_ENDPOINT = "/api/cortellis/searchresults/getSearchResults"

    def __init__(
        self,
        *,
        timeout: float = 30.0,
    ) -> None:

        headers = {
           "Accept": "application/json, text/plain, */*",
           "Content-Type": "application/json",
           "Origin": "https://www.cortellis.com",
           "Referer": "https://www.cortellis.com/intelligence/",
}

        cookie = os.getenv("CORTELLIS_COOKIE")
        authorization = os.getenv("CORTELLIS_AUTHORIZATION")
        

        if cookie:
            headers["Cookie"] = cookie
        
        if authorization:
            headers["Authorization"] = authorization


        self.client = httpx.Client(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "CortellisClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
    # =========================
    # Public API
    # =========================

    def search_drugs(
        self,
        query: str,
        *,
        page_number: int = 0,
        page_size: int = 25,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._search(
            query=query,
            component_key="drugSearch",
            section_key="drugSearchSection",
            page_number=page_number,
            page_size=page_size,
            filters=filters,
        )

    def search_trials(
        self,
        query: str,
        *,
        page_number: int = 0,
        page_size: int = 25,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._search(
            query=query,
            component_key="trialSearch",
            section_key="trialSearchSection",
            page_number=page_number,
            page_size=page_size,
            filters=filters,
        )

    def search_deals(
        self,
        query: str,
        *,
        page_number: int = 0,
        page_size: int = 25,
        filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._search(
            query=query,
            component_key="dealSearch",
            section_key="dealSearchSection",
            page_number=page_number,
            page_size=page_size,
            filters=filters,
        )

    # =========================
    # Internal
    # =========================

    def _search(
        self,
        *,
        query: str,
        component_key: str,
        section_key: str,
        page_number: int,
        page_size: int,
        filters: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:

        params = {
            "componentKey": component_key,
            "sectionKey": section_key,
            "displayType": "table",
        }

        # 浏览器里看到的形式是：
        # quickSearchQuery: "\"baricitinib\""
        quoted_query = f'"{query.strip()}"'

        payload = {
            "quickSearchQuery": quoted_query,
            "pageNumber": page_number,
            "pageSize": page_size,
            "quickSearchQueryType": "index",
            "filters": filters or [],
        }

        response = self.client.post(
            self.SEARCH_ENDPOINT,
            params=params,
            json=payload,
        )

        # 临时调试：看 Cortellis 为什么返回 403
        print("status:", response.status_code)
        print("content-type:", response.headers.get("content-type"))
        print("body:", response.text[:300])
        response.raise_for_status()

        data = response.json()


        




        if not isinstance(data, dict):
            raise ValueError(
                f"Unexpected Cortellis response type: {type(data)}"
            )

        return data