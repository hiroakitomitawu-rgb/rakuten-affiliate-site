"""
api_client.py
楽天市場 商品検索API クライアント

主な機能:
  - キーワード / ジャンルID を指定して商品を最大30件取得
  - 取得データを data/products_YYYYMMDD.json に保存
  - リトライ（Exponential Backoff）・レート制限・タイムアウト対応
  - 取得フィールド: 商品名・価格・商品URL・画像URL・レビュー評価・レビュー件数
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from requests.exceptions import (
    ConnectionError,
    HTTPError,
    ReadTimeout,
    RequestException,
)

from config import (
    MAX_HITS,
    MAX_RETRIES,
    RAKUTEN_API_BASE_URL,
    REQUEST_INTERVAL_SEC,
    REQUEST_TIMEOUT_SEC,
    RETRY_BACKOFF_SEC,
    AppConfig,
    get_genre_id,
    get_sort_key,
)

# ─────────────────────────────────────────────
# ロガー設定
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 商品データ型
# ─────────────────────────────────────────────
ProductItem = dict[str, Any]


# ─────────────────────────────────────────────
# メインクライアントクラス
# ─────────────────────────────────────────────
class RakutenAPIClient:
    """
    楽天市場 商品検索API クライアント。

    Examples
    --------
    >>> config = AppConfig()
    >>> client = RakutenAPIClient(config)
    >>> products = client.search(keyword="コーヒー", genre="food", sort="review_avg")
    >>> client.save(products)
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._last_request_time: float = 0.0

    # ─────────────────────────────────────────
    # 公開メソッド
    # ─────────────────────────────────────────

    def search(
        self,
        keyword: Optional[str] = None,
        genre: str = "all",
        sort: str = "standard",
        hits: int = MAX_HITS,
        page: int = 1,
    ) -> list[ProductItem]:
        """
        楽天商品を検索して正規化済み商品リストを返す。

        Parameters
        ----------
        keyword : str | None
            検索キーワード。None の場合はジャンル全体を取得。
        genre : str
            ジャンルキー（config.GENRE_MAP のキー）またはジャンルID数字文字列。
        sort : str
            ソートキー（config.SORT_MAP のキー）。
        hits : int
            取得件数（最大30）。
        page : int
            ページ番号（1始まり）。

        Returns
        -------
        list[ProductItem]
            正規化済み商品データのリスト。
        """
        hits = min(hits, MAX_HITS)
        genre_id = get_genre_id(genre)
        sort_param = get_sort_key(sort)

        params = self._build_params(
            keyword=keyword,
            genre_id=genre_id,
            sort=sort_param,
            hits=hits,
            page=page,
        )

        logger.info(
            "検索開始 | keyword=%s genre=%s(%s) sort=%s hits=%d page=%d",
            keyword or "(なし)", genre, genre_id, sort, hits, page,
        )

        raw_response = self._request_with_retry(params)
        products = self._parse_items(raw_response)

        logger.info("取得完了 | %d件", len(products))
        return products

    def save(
        self,
        products: list[ProductItem],
        filename: Optional[str] = None,
    ) -> Path:
        """
        商品リストを JSON ファイルに保存する。

        Parameters
        ----------
        products : list[ProductItem]
            保存する商品データリスト。
        filename : str | None
            保存ファイル名。None の場合は products_YYYYMMDD.json を自動生成。

        Returns
        -------
        Path
            保存先ファイルパス。
        """
        data_dir = Path(self.config.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"products_{date_str}.json"

        output_path = data_dir / filename

        payload = {
            "generated_at": datetime.now().isoformat(),
            "count": len(products),
            "products": products,
        }

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        logger.info("保存完了 | %s (%d件)", output_path, len(products))
        return output_path

    # ─────────────────────────────────────────
    # 内部メソッド
    # ─────────────────────────────────────────

    def _build_params(
        self,
        keyword: Optional[str],
        genre_id: str,
        sort: str,
        hits: int,
        page: int,
    ) -> dict[str, Any]:
        """APIリクエストパラメータを組み立てる。"""
        params: dict[str, Any] = {
            "applicationId": self.config.application_id,
            "format":        "json",
            "hits":          hits,
            "page":          page,
            "sort":          sort,
            "availability":  1,   # 在庫あり商品のみ
            "elements": (
                "itemName,itemPrice,itemUrl,"
                "mediumImageUrls,reviewAverage,reviewCount,"
                "shopName,itemCaption,pointRate"
            ),
        }

        if keyword:
            params["keyword"] = keyword

        if genre_id != "0":
            params["genreId"] = genre_id

        if self.config.affiliate_id:
            params["affiliateId"] = self.config.affiliate_id

        return params

    def _request_with_retry(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Exponential Backoff でリトライしながら API リクエストを送信する。

        Parameters
        ----------
        params : dict
            クエリパラメータ。

        Returns
        -------
        dict
            レスポンス JSON（パース済み）。

        Raises
        ------
        RuntimeError
            最大リトライ回数を超えた場合。
        """
        last_exception: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._rate_limit_wait()

                url = f"{RAKUTEN_API_BASE_URL}?{urlencode(params, doseq=True)}"
                logger.debug("リクエスト送信 (試行 %d/%d) | %s", attempt, MAX_RETRIES, url)

                response = self._session.get(url, timeout=REQUEST_TIMEOUT_SEC)
                self._last_request_time = time.monotonic()

                # HTTP エラーステータス (4xx / 5xx) を例外に変換
                response.raise_for_status()

                data = response.json()
                self._check_api_error(data)
                return data

            except HTTPError as e:
                status_code = e.response.status_code if e.response is not None else "N/A"
                logger.warning(
                    "HTTP エラー (試行 %d/%d) | status=%s %s",
                    attempt, MAX_RETRIES, status_code, e,
                )
                # 4xx クライアントエラーはリトライしない
                if e.response is not None and 400 <= e.response.status_code < 500:
                    raise RuntimeError(
                        f"クライアントエラー: HTTP {e.response.status_code}\n"
                        f"  → APIキーまたはパラメータを確認してください。\n"
                        f"  レスポンス: {e.response.text[:200]}"
                    ) from e
                last_exception = e

            except ReadTimeout as e:
                logger.warning(
                    "タイムアウト (試行 %d/%d) | timeout=%dsec",
                    attempt, MAX_RETRIES, REQUEST_TIMEOUT_SEC,
                )
                last_exception = e

            except ConnectionError as e:
                logger.warning(
                    "接続エラー (試行 %d/%d) | %s",
                    attempt, MAX_RETRIES, e,
                )
                last_exception = e

            except RequestException as e:
                logger.warning(
                    "リクエストエラー (試行 %d/%d) | %s",
                    attempt, MAX_RETRIES, e,
                )
                last_exception = e

            # 最後の試行でなければ待機してリトライ
            if attempt < MAX_RETRIES:
                wait_sec = RETRY_BACKOFF_SEC ** attempt
                logger.info("%.1f秒後にリトライします...", wait_sec)
                time.sleep(wait_sec)

        raise RuntimeError(
            f"APIリクエストが {MAX_RETRIES} 回失敗しました。\n"
            f"  最終エラー: {last_exception}"
        ) from last_exception

    def _rate_limit_wait(self) -> None:
        """前回リクエストから一定時間が経過するまで待機する（レート制限）。"""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < REQUEST_INTERVAL_SEC:
            sleep_time = REQUEST_INTERVAL_SEC - elapsed
            logger.debug("レート制限待機: %.3f秒", sleep_time)
            time.sleep(sleep_time)

    def _check_api_error(self, data: dict[str, Any]) -> None:
        """
        楽天 API 独自のエラーレスポンスを検出して例外を送出する。
        正常時は何もしない。
        """
        # エラー時は {"error": "...", "error_description": "..."} が返る
        if "error" in data:
            raise RuntimeError(
                f"楽天API エラー: {data.get('error')}\n"
                f"  詳細: {data.get('error_description', '(詳細なし)')}"
            )

    def _parse_items(self, data: dict[str, Any]) -> list[ProductItem]:
        """
        APIレスポンスから必要フィールドだけを抽出して正規化する。

        取得フィールド:
            item_name      : 商品名
            price          : 価格（円・int）
            item_url       : 商品URL（アフィリエイトリンク含む）
            image_url      : 商品画像URL（mediumサイズ）
            review_average : レビュー平均評価（float, 0.0〜5.0）
            review_count   : レビュー件数（int）
            shop_name      : ショップ名
            caption        : 商品説明文（最大500文字）
            point_rate     : ポイント倍率（int）
            fetched_at     : データ取得日時（ISO 8601）
        """
        items_raw = data.get("Items", [])
        fetched_at = datetime.now().isoformat()
        products: list[ProductItem] = []

        for wrapper in items_raw:
            # 楽天API は {"Item": {...}} 形式で各商品をラップする
            item = wrapper.get("Item", wrapper)

            # 画像URL の抽出（mediumImageUrls は [{"imageUrl": "..."}] のリスト）
            image_urls: list[str] = []
            for img in item.get("mediumImageUrls", []):
                if isinstance(img, dict):
                    url = img.get("imageUrl", "")
                elif isinstance(img, str):
                    url = img
                else:
                    url = ""
                if url:
                    # 楽天画像URLの末尾に付く ?_ex=128x128 のような拡大指定を除去
                    image_urls.append(url.split("?")[0])

            product: ProductItem = {
                "item_name":      str(item.get("itemName", "")).strip(),
                "price":          _to_int(item.get("itemPrice")),
                "item_url":       str(item.get("itemUrl", "")).strip(),
                "image_url":      image_urls[0] if image_urls else "",
                "image_urls":     image_urls,
                "review_average": _to_float(item.get("reviewAverage")),
                "review_count":   _to_int(item.get("reviewCount")),
                "shop_name":      str(item.get("shopName", "")).strip(),
                "caption":        str(item.get("itemCaption", ""))[:500].strip(),
                "point_rate":     _to_int(item.get("pointRate")),
                "fetched_at":     fetched_at,
            }
            products.append(product)

        return products

    def close(self) -> None:
        """HTTP セッションを閉じる。"""
        self._session.close()

    # context manager サポート
    def __enter__(self) -> "RakutenAPIClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# ─────────────────────────────────────────────
# ユーティリティ関数
# ─────────────────────────────────────────────

def _to_int(value: Any, default: int = 0) -> int:
    """値を int に変換する。変換不能な場合は default を返す。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    """値を float に変換する。変換不能な場合は default を返す。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
