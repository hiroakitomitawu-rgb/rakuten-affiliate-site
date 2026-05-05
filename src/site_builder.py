"""
site_builder.py
楽天アフィリエイトサイト トップページ（index.html）自動生成モジュール

処理フロー:
  1. data/products.json を読み込む
  2. articles/ の生成済みHTMLと突合してリンクURLを確定
  3. カテゴリ集計・価格帯集計を行う
  4. Jinja2 で templates/index.html をレンダリング
  5. docs/index.html（または指定先）に出力する

使い方:
  python src/site_builder.py
  python src/site_builder.py --data data/products.json --output docs --articles articles
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────

SITE_NAME      = "おすすめ商品レビュー"
SITE_HEADLINE  = "楽天市場の人気商品を徹底レビュー"
SITE_SUBHEADLINE = "実際のレビューをもとに、本当におすすめできる商品だけを厳選してご紹介します"

# 価格帯フィルタ定義（label, key, min, max）
PRICE_RANGES = [
    {"label": "すべて",        "key": "all",    "min": 0, "max": ""},
    {"label": "〜¥1,000",     "key": "u1000",  "min": 0,    "max": 1000},
    {"label": "¥1,000〜5,000","key": "1k5k",   "min": 1000, "max": 5000},
    {"label": "¥5,000〜10,000","key":"5k10k",  "min": 5000, "max": 10000},
    {"label": "¥10,000〜30,000","key":"10k30k","min": 10000,"max": 30000},
    {"label": "¥30,000〜",    "key": "o30k",   "min": 30000,"max": ""},
]

# 楽天ジャンルIDからカテゴリ表示名へのマッピング
GENRE_LABEL_MAP: dict[str, str] = {
    "food":       "食品・グルメ",
    "sweets":     "スイーツ",
    "health":     "健康・ダイエット",
    "beauty":     "コスメ・美容",
    "lady_fashion": "レディースファッション",
    "men_fashion":  "メンズファッション",
    "shoes":      "シューズ",
    "bag":        "バッグ・財布",
    "sports":     "スポーツ",
    "golf":       "ゴルフ",
    "toy":        "おもちゃ",
    "pc":         "パソコン・周辺機器",
    "smartphone": "スマートフォン",
    "camera":     "カメラ",
    "appliance":  "家電",
    "kitchen":    "キッチン・日用品",
    "interior":   "インテリア",
    "book":       "本・雑誌",
    "game":       "ゲーム",
    "baby":       "ベビー・キッズ",
    "pet":        "ペット用品",
    "car":        "カー・バイク",
    "other":      "その他",
}

# ショップ名・商品名キーワードからカテゴリを推定するルール
# （楽天APIのジャンルIDが取得できない場合のフォールバック）
KEYWORD_CATEGORY_RULES: list[tuple[str, str]] = [
    (r"プロテイン|サプリ|ダイエット|ビタミン",    "health"),
    (r"コーヒー|お茶|ジュース|飲料|食品|菓子|スイーツ|チョコ|クッキー", "food"),
    (r"コスメ|美容|スキンケア|化粧|シャンプー",  "beauty"),
    (r"シューズ|スニーカー|靴|サンダル",          "shoes"),
    (r"バッグ|財布|ポーチ|かばん",                "bag"),
    (r"スポーツ|ヨガ|フィットネス|アウトドア|テント|リュック", "sports"),
    (r"パソコン|PC|マウス|キーボード|モニター",   "pc"),
    (r"スマホ|iPhone|Android|ケース",             "smartphone"),
    (r"カメラ|レンズ|三脚",                        "camera"),
    (r"冷蔵庫|洗濯機|掃除機|電子レンジ|エアコン|家電", "appliance"),
    (r"鍋|フライパン|包丁|食器|調理",             "kitchen"),
    (r"ソファ|ベッド|カーテン|照明|インテリア",   "interior"),
    (r"おもちゃ|ゲーム|ぬいぐるみ|レゴ",          "toy"),
    (r"絵本|小説|漫画|雑誌|本",                   "book"),
    (r"ベビー|オムツ|哺乳瓶|チャイルドシート",   "baby"),
    (r"ペット|犬|猫|ドッグ|キャット",             "pet"),
]


# ─────────────────────────────────────────────
# Jinja2 カスタムフィルター
# ─────────────────────────────────────────────

def _filter_format_price(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)

def _filter_format_number(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


# ─────────────────────────────────────────────
# カテゴリ推定
# ─────────────────────────────────────────────

def guess_category(item: dict[str, Any]) -> str:
    """
    商品名・ショップ名・説明文からカテゴリキーを推定する。
    推定できない場合は 'other' を返す。
    """
    text = " ".join([
        item.get("item_name", ""),
        item.get("shop_name", ""),
        item.get("caption", ""),
    ])
    for pattern, category in KEYWORD_CATEGORY_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    return "other"


# ─────────────────────────────────────────────
# 記事URLの解決
# ─────────────────────────────────────────────

def resolve_article_url(
    item: dict[str, Any],
    articles_dir: Optional[Path],
    articles_prefix: str = "articles/",
) -> str:
    """
    商品データに対応する記事 HTML ファイルのURLを解決する。
    articles/ ディレクトリが指定されている場合はスラグで検索する。
    見つからない場合は楽天商品URLにフォールバックする。
    """
    if articles_dir and articles_dir.exists():
        # article_generator.py と同じスラグ生成ロジック
        import hashlib
        url = item.get("item_url", "")
        slug = ""
        if url:
            path = urlparse(url).path.rstrip("/")
            candidate = path.split("/")[-1] if path else ""
            if candidate and re.match(r"^[\w\-]+$", candidate):
                slug = candidate[:80]
        if not slug:
            name = item.get("item_name", "product")
            slug = hashlib.md5(name.encode()).hexdigest()[:12]

        html_path = articles_dir / f"{slug}.html"
        if html_path.exists():
            return f"{articles_prefix}{slug}.html"

    # フォールバック: 楽天商品URL
    return item.get("item_url", "#")


# ─────────────────────────────────────────────
# メイン生成クラス
# ─────────────────────────────────────────────

class SiteBuilder:
    """
    products.json を読み込んでトップページ (index.html) を生成するクラス。

    Examples
    --------
    >>> builder = SiteBuilder(
    ...     data_path="data/products.json",
    ...     output_path="docs/index.html",
    ...     articles_dir="articles",
    ...     template_dir="templates",
    ... )
    >>> builder.build()
    """

    def __init__(
        self,
        data_path:    str | Path = "data/products.json",
        output_path:  str | Path = "docs/index.html",
        articles_dir: str | Path = "articles",
        template_dir: str | Path = "templates",
        site_name:    str = SITE_NAME,
        site_headline: str = SITE_HEADLINE,
        site_subheadline: str = SITE_SUBHEADLINE,
        articles_prefix: str = "articles/",
        static_prefix:   str = "static/",
    ) -> None:
        self.data_path        = Path(data_path)
        self.output_path      = Path(output_path)
        self.articles_dir     = Path(articles_dir)
        self.template_dir     = Path(template_dir)
        self.site_name        = site_name
        self.site_headline    = site_headline
        self.site_subheadline = site_subheadline
        self.articles_prefix  = articles_prefix
        self.static_prefix    = static_prefix
        self._env = self._build_jinja_env()

    # ─────────────────────────────────────────
    # 公開メソッド
    # ─────────────────────────────────────────

    def build(self) -> Path:
        """
        index.html を生成して保存し、出力パスを返す。
        """
        products_raw = self._load_products()
        products     = self._enrich_products(products_raw)
        context      = self._build_context(products)

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        template = self._env.get_template("index.html")
        html = template.render(**context)
        self.output_path.write_text(html, encoding="utf-8")

        logger.info("トップページを生成しました: %s (%d件)", self.output_path, len(products))
        return self.output_path

    # ─────────────────────────────────────────
    # 内部メソッド
    # ─────────────────────────────────────────

    def _build_jinja_env(self) -> Environment:
        env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html"]),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        env.filters["format_price"]  = _filter_format_price
        env.filters["format_number"] = _filter_format_number
        return env

    def _load_products(self) -> list[dict[str, Any]]:
        """products.json を読み込んで商品リストを返す。"""
        if not self.data_path.exists():
            raise FileNotFoundError(
                f"商品データが見つかりません: {self.data_path}\n"
                "  先に generate.py を実行してください。"
            )
        with self.data_path.open(encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("products", data) if isinstance(data, dict) else data
        logger.info("商品データ読み込み: %d件", len(items))
        return items

    def _enrich_products(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        各商品にカテゴリ情報・記事URLを付与して返す。
        新着順（fetched_at の降順）にソートする。
        """
        enriched = []
        for item in items:
            category_key   = guess_category(item)
            category_label = GENRE_LABEL_MAP.get(category_key, "その他")
            article_url    = resolve_article_url(
                item,
                self.articles_dir,
                self.articles_prefix,
            )
            enriched.append({
                **item,
                "category_key":   category_key,
                "category_label": category_label,
                "article_url":    article_url,
            })

        # 新着順（fetched_at 降順）
        enriched.sort(
            key=lambda x: x.get("fetched_at", ""),
            reverse=True,
        )
        return enriched

    def _build_categories(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        カテゴリごとの件数を集計して、フィルタボタン用リストを返す。
        件数 0 のカテゴリは除外。件数の多い順に並べる。
        """
        count: dict[str, int] = {}
        for p in products:
            key = p["category_key"]
            count[key] = count.get(key, 0) + 1

        categories = []
        for key, cnt in sorted(count.items(), key=lambda x: -x[1]):
            categories.append({
                "key":   key,
                "label": GENRE_LABEL_MAP.get(key, "その他"),
                "count": cnt,
            })
        return categories

    def _build_stats(self, products: list[dict[str, Any]]) -> dict[str, Any]:
        """サマリー統計（合計件数・平均評価・最終更新）を計算する。"""
        total = len(products)
        if total == 0:
            return {"total": 0, "avg_rating": "0.0", "last_updated": "—"}

        avg = sum(p.get("review_average", 0.0) for p in products) / total
        dates = [p.get("fetched_at", "") for p in products if p.get("fetched_at")]
        last_updated = (
            datetime.fromisoformat(max(dates)).strftime("%Y年%m月%d日")
            if dates else "—"
        )
        return {
            "total":        total,
            "avg_rating":   f"{avg:.1f}",
            "last_updated": last_updated,
        }

    def _build_structured_data(self, products: list[dict[str, Any]]) -> str:
        """WebSite + ItemList の構造化データ（JSON-LD）を生成する。"""
        item_list = []
        for idx, p in enumerate(products[:10]):  # 先頭10件のみ
            item_list.append({
                "@type":    "ListItem",
                "position": idx + 1,
                "name":     p.get("item_name", ""),
                "url":      p.get("article_url", p.get("item_url", "")),
                "image":    p.get("image_url", ""),
            })

        data = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "WebSite",
                    "name":  self.site_name,
                    "description": self.site_subheadline,
                    "potentialAction": {
                        "@type":       "SearchAction",
                        "target":      "{search_term_string}",
                        "query-input": "required name=search_term_string",
                    },
                },
                {
                    "@type":           "ItemList",
                    "name":            self.site_headline,
                    "numberOfItems":   len(products),
                    "itemListElement": item_list,
                },
            ],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _build_context(self, products: list[dict[str, Any]]) -> dict[str, Any]:
        """Jinja2 テンプレートに渡すコンテキスト変数を構築する。"""
        stats      = self._build_stats(products)
        categories = self._build_categories(products)

        return {
            # ── SEO メタ ──────────────────────────
            "meta_title": (
                f"楽天市場 おすすめ商品レビュー一覧 {stats['total']}件"
                f"｜{self.site_name}"
            ),
            "meta_description": (
                f"楽天市場の人気商品{stats['total']}件を徹底レビュー。"
                "価格・評価・使用感を正直にまとめました。"
                f"平均評価{stats['avg_rating']}点の厳選商品のみ掲載。"
            ),
            "structured_data": self._build_structured_data(products),

            # ── サイト情報 ────────────────────────
            "site_name":        self.site_name,
            "site_headline":    self.site_headline,
            "site_subheadline": self.site_subheadline,
            "year":             date.today().year,

            # ── サマリー統計 ──────────────────────
            "total_products":   stats["total"],
            "avg_rating":       stats["avg_rating"],
            "last_updated":     stats["last_updated"],

            # ── フィルタ用データ ──────────────────
            "categories":       categories,
            "price_ranges":     PRICE_RANGES,

            # ── 商品リスト ────────────────────────
            "products":         products,
        }


# ─────────────────────────────────────────────
# CLI エントリーポイント
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    sys.path.insert(0, str(Path(__file__).parent))

    parser = argparse.ArgumentParser(description="products.json からトップページを生成します")
    parser.add_argument("--data",      default="data/products.json", help="入力JSONファイル")
    parser.add_argument("--output",    default="docs/index.html",    help="出力HTMLファイル")
    parser.add_argument("--articles",  default="articles",           help="記事ディレクトリ")
    parser.add_argument("--templates", default="templates",          help="テンプレートディレクトリ")
    parser.add_argument("--articles-prefix", default="articles/",   help="記事URLのプレフィックス")
    parser.add_argument("--site-name", default=SITE_NAME,            help="サイト名")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    builder = SiteBuilder(
        data_path=args.data,
        output_path=args.output,
        articles_dir=args.articles,
        template_dir=args.templates,
        articles_prefix=args.articles_prefix,
        site_name=args.site_name,
    )

    try:
        out = builder.build()
        size_kb = out.stat().st_size / 1024
        print(f"\n✓ トップページを生成しました")
        print(f"  → {out}  ({size_kb:.1f} KB)")
    except FileNotFoundError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
