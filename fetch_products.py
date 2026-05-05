#!/usr/bin/env python3
"""
fetch_products.py
GitHub Actions Step 1: 楽天APIから最新商品データを取得して保存する

使い方:
  python fetch_products.py                          # デフォルト設定で実行
  python fetch_products.py --config fetch_config.json   # 設定ファイル指定

環境変数:
  RAKUTEN_APPLICATION_ID   楽天アプリID（必須）
  RAKUTEN_AFFILIATE_ID     楽天アフィリエイトID（省略可）

設定ファイル (fetch_config.json) の例:
  {
    "searches": [
      {"keyword": "プロテイン",   "genre": "health",  "sort": "review_avg", "hits": 30},
      {"keyword": "コーヒーメーカー", "genre": "kitchen", "sort": "review_count","hits": 20}
    ]
  }
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# src/ を import パスに追加
sys.path.insert(0, str(Path(__file__).parent / "src"))

from api_client import RakutenAPIClient
from config import AppConfig

# ─────────────────────────────────────────────
# デフォルト検索設定（fetch_config.json がない場合に使用）
# ─────────────────────────────────────────────
DEFAULT_SEARCHES = [
    {"keyword": "プロテイン",        "genre": "health",    "sort": "review_avg",   "hits": 30},
    {"keyword": "コーヒーメーカー",   "genre": "kitchen",   "sort": "review_count", "hits": 20},
    {"keyword": "ワイヤレスイヤホン", "genre": "smartphone","sort": "review_avg",   "hits": 20},
    {"keyword": "ヨガマット",        "genre": "sports",    "sort": "affiliation",  "hits": 15},
    {"keyword": "スキンケア",        "genre": "beauty",    "sort": "review_avg",   "hits": 15},
]

OUTPUT_FILE = "data/products.json"
CONFIG_FILE = "fetch_config.json"


def load_search_config() -> list[dict]:
    """fetch_config.json があれば読み込み、なければデフォルトを返す。"""
    config_path = Path(CONFIG_FILE)
    if config_path.exists():
        with config_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        searches = cfg.get("searches", DEFAULT_SEARCHES)
        logging.info("設定ファイルを読み込みました: %s (%d件の検索)", CONFIG_FILE, len(searches))
        return searches
    logging.info("設定ファイルなし → デフォルト検索設定を使用 (%d件)", len(DEFAULT_SEARCHES))
    return DEFAULT_SEARCHES


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. 設定読み込み
    searches = load_search_config()

    # 2. AppConfig（環境変数から RAKUTEN_APPLICATION_ID を取得）
    try:
        config = AppConfig()
    except EnvironmentError as e:
        logging.error("%s", e)
        return 1

    # 3. API 取得 & マージ
    all_products: list[dict] = []
    seen_urls: set[str] = set()   # URL で重複排除

    with RakutenAPIClient(config) as client:
        for i, search in enumerate(searches, 1):
            keyword = search.get("keyword")
            genre   = search.get("genre",   "all")
            sort    = search.get("sort",    "standard")
            hits    = int(search.get("hits", 30))

            logging.info(
                "[%d/%d] 検索: keyword=%s genre=%s sort=%s hits=%d",
                i, len(searches), keyword or "(なし)", genre, sort, hits,
            )
            try:
                products = client.search(keyword=keyword, genre=genre, sort=sort, hits=hits)
                before = len(all_products)
                for p in products:
                    url = p.get("item_url", "")
                    if url not in seen_urls:
                        seen_urls.add(url)
                        all_products.append(p)
                logging.info("  → %d件取得（重複除外後 +%d件）", len(products), len(all_products) - before)
            except RuntimeError as e:
                # 1件の検索が失敗しても続行
                logging.warning("  検索スキップ: %s", e)

    if not all_products:
        logging.error("商品データを1件も取得できませんでした")
        return 1

    # 4. 保存（article_generator が期待する形式）
    out_path = Path(OUTPUT_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from datetime import datetime
    payload = {
        "generated_at": datetime.now().isoformat(),
        "count": len(all_products),
        "products": all_products,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("保存完了: %s (%d件)", out_path, len(all_products))

    # GitHub Actions のステップサマリーに出力
    summary_path = Path(sys.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"))
    try:
        with summary_path.open("a", encoding="utf-8") as f:
            f.write(f"### ✅ Step 1: 商品データ取得完了\n")
            f.write(f"- 取得商品数: **{len(all_products)}件**\n")
            f.write(f"- 検索クエリ数: {len(searches)}\n\n")
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
