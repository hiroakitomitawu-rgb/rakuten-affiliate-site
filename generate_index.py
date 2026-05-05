#!/usr/bin/env python3
"""
generate_index.py
GitHub Actions Step 3: products.json からトップページ (docs/index.html) を生成する

使い方:
  python generate_index.py
  python generate_index.py --data data/products.json --output docs/index.html
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from site_builder import SiteBuilder


def main() -> int:
    parser = argparse.ArgumentParser(description="トップページ index.html を生成します")
    parser.add_argument("--data",             default="data/products.json")
    parser.add_argument("--output",           default="docs/index.html")
    parser.add_argument("--articles-dir",     default="docs/articles")
    parser.add_argument("--templates",        default="templates")
    parser.add_argument("--articles-prefix",  default="articles/")
    parser.add_argument("--site-name",        default="おすすめ商品レビュー")
    parser.add_argument("--log-level",        default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        builder = SiteBuilder(
            data_path=args.data,
            output_path=args.output,
            articles_dir=args.articles_dir,
            template_dir=args.templates,
            articles_prefix=args.articles_prefix,
            site_name=args.site_name,
        )
        out = builder.build()
    except FileNotFoundError as e:
        logging.error("%s", e)
        return 1

    size_kb = out.stat().st_size / 1024
    logging.info("トップページ生成完了: %s (%.1f KB)", out, size_kb)

    # GitHub Actions ステップサマリー
    summary_path = Path(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"))
    try:
        with summary_path.open("a", encoding="utf-8") as f:
            f.write(f"### ✅ Step 3: トップページ生成完了\n")
            f.write(f"- 出力先: `{out}`\n")
            f.write(f"- ファイルサイズ: {size_kb:.1f} KB\n\n")
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
