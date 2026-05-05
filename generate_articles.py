#!/usr/bin/env python3
"""
generate_articles.py
GitHub Actions Step 2: products.json から個別記事 HTML を生成する

使い方:
  python generate_articles.py
  python generate_articles.py --data data/products.json --output docs/articles

環境変数:
  RAKUTEN_AFFILIATE_ID     楽天アフィリエイトID（省略可）
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from article_generator import ArticleGenerator


def main() -> int:
    parser = argparse.ArgumentParser(description="products.json から記事HTMLを生成します")
    parser.add_argument("--data",          default="data/products.json")
    parser.add_argument("--output",        default="docs/articles")
    parser.add_argument("--templates",     default="templates")
    parser.add_argument("--affiliate-id",  default=None)
    parser.add_argument("--static-root",   default="../static")
    parser.add_argument("--site-root",     default="../")
    parser.add_argument("--log-level",     default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    affiliate_id = args.affiliate_id or os.environ.get("RAKUTEN_AFFILIATE_ID")

    try:
        gen = ArticleGenerator(
            data_path=args.data,
            output_dir=args.output,
            template_dir=args.templates,
            static_root=args.static_root,
            site_root=args.site_root,
            affiliate_id=affiliate_id,
        )
        results = gen.run()
    except FileNotFoundError as e:
        logging.error("%s", e)
        return 1

    if not results:
        logging.warning("記事が1件も生成されませんでした")
        return 1

    logging.info("=== 記事生成完了: %d件 ===", len(results))

    # GitHub Actions ステップサマリー
    summary_path = Path(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"))
    try:
        with summary_path.open("a", encoding="utf-8") as f:
            f.write(f"### ✅ Step 2: 記事HTML生成完了\n")
            f.write(f"- 生成記事数: **{len(results)}件**\n")
            f.write(f"- 出力先: `{args.output}/`\n\n")
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
