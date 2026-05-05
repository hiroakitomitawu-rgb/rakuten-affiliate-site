"""
article_generator.py
楽天商品JSONデータ → SEO記事HTML 自動生成モジュール

処理フロー:
  1. data/products.json を読み込む
  2. 各商品ごとにコンテンツを生成（タイトル・導入文・ポイント等）
  3. Jinja2 テンプレートに流し込んで HTML 出力
  4. articles/{item_id}.html として保存
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import textwrap
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    select_autoescape,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────
TITLE_MAX_LEN   = 60    # SEO タイトル上限
META_DESC_LEN   = 120   # meta description 推奨長
INTRO_TARGET    = 200   # 導入文 目標文字数
SUMMARY_TARGET  = 100   # まとめ文 目標文字数

# 楽天アフィリエイトリンクのベースURL
RAKUTEN_AFFILIATE_BASE = "https://hb.afl.rakuten.co.jp/hgc/"

# ─────────────────────────────────────────────
# Jinja2 カスタムフィルター
# ─────────────────────────────────────────────

def _filter_format_price(value: Any) -> str:
    """数値を 3 桁カンマ区切りの文字列に変換する。"""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _filter_truncate_str(value: str, length: int, suffix: str = "…") -> str:
    """文字列を指定文字数で切り詰める。"""
    if len(value) <= length:
        return value
    return value[:length - len(suffix)] + suffix


# ─────────────────────────────────────────────
# コンテンツ生成ユーティリティ
# ─────────────────────────────────────────────

class ContentBuilder:
    """
    商品データ(dict) から記事コンテンツ各部を生成するクラス。

    楽天APIのみ使用（外部AIサービス不使用）。
    商品名・価格・レビュー・説明文からルールベースで文章を組み立てる。
    """

    # ── 導入文テンプレート（{item}・{pain}・{solution} を置換）──
    _INTRO_TEMPLATES = [
        (
            "「{item}を選ぶとき、どれが本当に良いのか分からない…」そんな悩みを抱えていませんか？"
            "商品が多すぎて比較に疲れてしまう方も多いと思います。"
            "この記事では、楽天市場で{review_count}件以上のレビューを集めた"
            "人気商品『{item}』を徹底レビューします。"
            "購入前に知っておきたいポイントをまとめましたので、ぜひ参考にしてください。"
        ),
        (
            "{item}の購入を検討しているあなたへ。"
            "「本当に使えるの？」「値段に見合う？」という不安は当然です。"
            "実際のユーザー評価（平均{review_avg}点・{review_count}件）をもとに、"
            "この商品の魅力と注意点を正直にお伝えします。"
        ),
        (
            "毎日の生活をもっと快適にしたいと思ったとき、頼りになる一品があります。"
            "それが『{item}』。楽天市場でも高評価（{review_avg}点）を獲得しており、"
            "多くのユーザーから支持されています。"
            "この記事では価格・機能・使い勝手をわかりやすく解説します。"
        ),
    ]

    # ── おすすめポイントテンプレート ──
    _POINT_TEMPLATES = [
        {
            "title": "楽天ユーザーから高評価！{review_count}件の実績",
            "body":  (
                "楽天市場で{review_count}件ものレビューを獲得し、平均評価{review_avg}点を誇ります。"
                "多くのユーザーが実際に使用して満足しているという実績は、"
                "購入前の大きな安心材料になります。"
            ),
        },
        {
            "title": "コストパフォーマンスの高さ",
            "body":  (
                "価格は税込{price}円と、品質に対してリーズナブルな設定です。"
                "毎日使うものだからこそ、コスパの良さは重要なポイント。"
                "楽天ポイントも貯まるので、さらにお得に購入できます。"
            ),
        },
        {
            "title": "信頼のショップ「{shop_name}」が販売",
            "body":  (
                "販売元の「{shop_name}」は楽天市場で多くの実績を持つ信頼のショップです。"
                "丁寧な梱包と迅速な発送で、安心してお買い物いただけます。"
                "アフターフォローも充実しているので初めての方でも安心です。"
            ),
        },
    ]

    # ── ターゲットユーザーテンプレート ──
    _TARGET_TEMPLATES = [
        "{item}をはじめて購入する方",
        "コストパフォーマンスを重視している方",
        "レビュー件数の多い人気商品を探している方",
        "楽天ポイントをお得に貯めたい方",
        "品質にこだわりながら価格も抑えたい方",
    ]
    _NOT_FOR_TEMPLATES = [
        "すでに同等品をお持ちの方",
        "最高グレードのみを求める方",
    ]

    # ── まとめ文テンプレート ──
    _SUMMARY_TEMPLATES = [
        (
            "『{item}』は{review_count}件のレビューと{review_avg}点の高評価が示す通り、"
            "多くの方に愛されている人気商品です。"
            "コスパも◎で楽天市場でお得に購入できます。ぜひ試してみてください。"
        ),
        (
            "以上、『{item}』のレビューをお届けしました。"
            "価格{price}円で{review_avg}点の評価を誇るこの商品、"
            "迷っているなら楽天市場でまず価格をチェックしてみましょう。"
        ),
    ]

    def __init__(self, item: dict[str, Any], idx: int = 0) -> None:
        self.item = item
        self.idx  = idx  # テンプレートローテーション用インデックス

    # ── ショートカットプロパティ ──
    @property
    def name(self) -> str:
        return self.item.get("item_name", "")

    @property
    def price(self) -> int:
        return int(self.item.get("price", 0))

    @property
    def review_avg(self) -> float:
        return float(self.item.get("review_average", 0.0))

    @property
    def review_count(self) -> int:
        return int(self.item.get("review_count", 0))

    @property
    def shop_name(self) -> str:
        return self.item.get("shop_name", "楽天ショップ")

    @property
    def _fmt(self) -> dict[str, Any]:
        """テンプレート文字列に埋め込む共通変数。"""
        return {
            "item":         self.name,
            "price":        f"{self.price:,}",
            "review_avg":   self.review_avg,
            "review_count": f"{self.review_count:,}",
            "shop_name":    self.shop_name,
        }

    # ── 各セクション生成 ────────────────────────

    def build_article_title(self) -> str:
        """
        SEO タイトルを生成する（60文字以内）。
        商品名 + メリットフレーズ を組み合わせる。
        """
        benefits = [
            "の口コミ・レビュー【徹底解説】",
            "はなぜ人気？特徴とおすすめポイント",
            "を徹底レビュー！買う前に知りたいこと",
            "の評判・特徴まとめ",
        ]
        benefit = benefits[self.idx % len(benefits)]
        title = f"{self.name}{benefit}"
        if len(title) <= TITLE_MAX_LEN:
            return title
        # 商品名を短縮して収める
        allowed = TITLE_MAX_LEN - len(benefit)
        short_name = self.name[:allowed] if allowed > 5 else self.name[:TITLE_MAX_LEN]
        return f"{short_name}{benefit}"[:TITLE_MAX_LEN]

    def build_article_subtitle(self) -> str:
        """h1 直下の補足サブタイトル。"""
        return (
            f"楽天市場で{self.review_count:,}件の評価・平均{self.review_avg}点を獲得した人気商品を徹底解説"
        )

    def build_meta_title(self) -> str:
        """<title> タグ用。サイト名を末尾に付加。"""
        base = self.build_article_title()
        suffix = "｜おすすめ商品レビュー"
        full = base + suffix
        return full if len(full) <= 70 else base

    def build_meta_description(self) -> str:
        """meta description（120文字以内）。"""
        desc = (
            f"楽天市場の人気商品『{self.name}』をレビュー。"
            f"評価{self.review_avg}点・{self.review_count:,}件のレビューを分析し、"
            f"おすすめポイントと購入前の注意点をわかりやすく解説します。"
        )
        return desc[:META_DESC_LEN]

    def build_intro_paragraphs(self) -> list[str]:
        """導入文を段落リストで返す（合計 ~200字）。"""
        tpl = self._INTRO_TEMPLATES[self.idx % len(self._INTRO_TEMPLATES)]
        full_text = tpl.format(**self._fmt)
        # 200字程度に調整して2段落に分割
        full_text = full_text[:INTRO_TARGET * 2]  # 上限
        # 「。」で分割して2段落に
        sentences = full_text.split("。")
        mid = max(1, len(sentences) // 2)
        para1 = "。".join(sentences[:mid]) + "。"
        para2 = "。".join(sentences[mid:]).strip()
        if para2 and not para2.endswith("。"):
            para2 += "。"
        return [p for p in [para1, para2] if p.strip("。")]

    def build_recommend_points(self) -> list[dict[str, str]]:
        """おすすめポイント3件を返す。"""
        points = []
        for tpl in self._POINT_TEMPLATES:
            points.append({
                "title": tpl["title"].format(**self._fmt),
                "body":  tpl["body"].format(**self._fmt),
            })
        return points

    def build_target_users(self) -> list[str]:
        """こんな人におすすめ リストを返す。"""
        return [
            t.format(item=self.name) if "{item}" in t else t
            for t in self._TARGET_TEMPLATES
        ]

    def build_not_for_users(self) -> list[str]:
        """向かない人リストを返す。"""
        return self._NOT_FOR_TEMPLATES[:]

    def build_summary_text(self) -> str:
        """まとめ文（~100字）。"""
        tpl = self._SUMMARY_TEMPLATES[self.idx % len(self._SUMMARY_TEMPLATES)]
        text = tpl.format(**self._fmt)
        return text[:SUMMARY_TARGET * 2]  # 上限

    def build_image_alt(self) -> str:
        """商品画像の alt テキスト。"""
        return f"{self.name}の商品画像"

    def build_spec_extra_rows(self) -> list[dict[str, str]]:
        """スペック表の追加行（caption から抽出）。"""
        rows: list[dict[str, str]] = []
        caption = self.item.get("caption", "")
        # 容量・サイズ・素材などを caption から簡易抽出（正規表現）
        patterns = [
            (r"(\d+(?:\.\d+)?)\s*[gG](?:[rR](?:am)?)?(?![hHzZ])",    "内容量", lambda m: f"{m.group(0)}"),
            (r"(\d+(?:\.\d+)?)\s*[kK][gG]",                           "重量",   lambda m: f"{m.group(0)}"),
            (r"(\d+(?:\.\d+)?)\s*[mM][lL]",                           "容量",   lambda m: f"{m.group(0)}"),
            (r"(\d+)\s*個(?:入|セット|パック)",                        "セット内容", lambda m: f"{m.group(0)}"),
        ]
        for pattern, label, formatter in patterns:
            m = re.search(pattern, caption)
            if m:
                rows.append({"label": label, "value": formatter(m)})
        return rows[:3]  # 最大3行


# ─────────────────────────────────────────────
# アフィリエイト URL 生成
# ─────────────────────────────────────────────

def build_affiliate_url(item_url: str, affiliate_id: Optional[str]) -> str:
    """
    楽天アフィリエイトリンクURLを組み立てる。
    affiliate_id が未設定の場合は元の商品URLをそのまま返す。
    """
    if not affiliate_id:
        return item_url
    encoded = quote(item_url, safe="")
    return f"{RAKUTEN_AFFILIATE_BASE}{affiliate_id}/?pc={encoded}&m={encoded}"


# ─────────────────────────────────────────────
# 記事ファイル名 / ID 生成
# ─────────────────────────────────────────────

def item_to_slug(item: dict[str, Any]) -> str:
    """
    商品データからファイル名用スラグを生成する。
    楽天商品URLの末尾パスを使用し、取得できない場合は商品名の MD5 を使用。
    """
    url = item.get("item_url", "")
    if url:
        path = urlparse(url).path.rstrip("/")
        slug = path.split("/")[-1] if path else ""
        if slug and re.match(r"^[\w\-]+$", slug):
            return slug[:80]
    # フォールバック: 商品名の MD5 先頭12文字
    name = item.get("item_name", "product")
    return hashlib.md5(name.encode()).hexdigest()[:12]


# ─────────────────────────────────────────────
# メイン生成クラス
# ─────────────────────────────────────────────

class ArticleGenerator:
    """
    products.json を読み込み、articles/ 以下に HTML 記事を生成するクラス。

    Examples
    --------
    >>> gen = ArticleGenerator(
    ...     data_path="data/products.json",
    ...     output_dir="articles",
    ...     template_dir="templates",
    ...     affiliate_id="your_affiliate_id",
    ...     site_root="../",
    ... )
    >>> results = gen.run()
    """

    def __init__(
        self,
        data_path: str | Path = "data/products.json",
        output_dir: str | Path = "articles",
        template_dir: str | Path = "templates",
        static_root: str = "../static",
        site_root: str = "../",
        site_name: str = "おすすめ商品レビュー",
        affiliate_id: Optional[str] = None,
    ) -> None:
        self.data_path    = Path(data_path)
        self.output_dir   = Path(output_dir)
        self.template_dir = Path(template_dir)
        self.static_root  = static_root
        self.site_root    = site_root
        self.site_name    = site_name
        self.affiliate_id = affiliate_id

        self._env = self._build_jinja_env()

    # ─────────────────────────────────────────
    # 公開メソッド
    # ─────────────────────────────────────────

    def run(self) -> list[Path]:
        """
        全商品の記事を生成してパスリストを返す。

        Returns
        -------
        list[Path]
            生成された HTML ファイルのパスリスト。
        """
        products = self._load_products()
        if not products:
            logger.warning("商品データが空です: %s", self.data_path)
            return []

        self.output_dir.mkdir(parents=True, exist_ok=True)
        generated: list[Path] = []

        for idx, item in enumerate(products):
            try:
                path = self._generate_article(item, idx)
                generated.append(path)
                logger.info("[%d/%d] 生成完了: %s", idx + 1, len(products), path.name)
            except Exception as e:
                logger.error("[%d/%d] 生成失敗 (%s): %s", idx + 1, len(products),
                             item.get("item_name", "?"), e)

        logger.info("=== 生成完了: %d件 ===", len(generated))
        return generated

    def generate_one(self, item: dict[str, Any], idx: int = 0) -> str:
        """
        単一商品の記事 HTML 文字列を返す（テスト・プレビュー用）。
        """
        context = self._build_context(item, idx)
        template = self._env.get_template("article.html")
        return template.render(**context)

    # ─────────────────────────────────────────
    # 内部メソッド
    # ─────────────────────────────────────────

    def _build_jinja_env(self) -> Environment:
        """Jinja2 環境を構築してカスタムフィルターを登録する。"""
        env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html"]),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        env.filters["format_price"] = _filter_format_price
        env.filters["truncate_str"] = _filter_truncate_str
        return env

    def _load_products(self) -> list[dict[str, Any]]:
        """products.json を読み込んで商品リストを返す。"""
        if not self.data_path.exists():
            raise FileNotFoundError(
                f"商品データファイルが見つかりません: {self.data_path}\n"
                "  先に generate.py を実行して products.json を生成してください。"
            )
        with self.data_path.open(encoding="utf-8") as f:
            data = json.load(f)

        # {"products": [...]} 形式と [...] 形式の両方を受け付ける
        if isinstance(data, dict):
            products = data.get("products", [])
        elif isinstance(data, list):
            products = data
        else:
            raise ValueError("products.json の形式が不正です。")

        logger.info("商品データ読み込み: %d件 (%s)", len(products), self.data_path)
        return products

    def _generate_article(self, item: dict[str, Any], idx: int) -> Path:
        """1商品の HTML ファイルを生成して保存する。"""
        html = self.generate_one(item, idx)
        slug = item_to_slug(item)
        output_path = self.output_dir / f"{slug}.html"
        output_path.write_text(html, encoding="utf-8")
        return output_path

    def _build_context(self, item: dict[str, Any], idx: int) -> dict[str, Any]:
        """Jinja2 テンプレートに渡すコンテキスト変数を構築する。"""
        cb = ContentBuilder(item, idx)
        now = datetime.now()
        today = date.today()

        return {
            # ── SEO メタ ──────────────────────────
            "meta_title":       cb.build_meta_title(),
            "meta_description": cb.build_meta_description(),
            "og_type":          "article",
            "og_image":         item.get("image_url", ""),
            "structured_data":  self._build_structured_data(item, cb),

            # ── サイト共通 ────────────────────────
            "site_name":        self.site_name,
            "site_root":        self.site_root,
            "static_root":      self.static_root,
            "now":              now,

            # ── パンくずリスト ─────────────────────
            "breadcrumbs": [
                {"name": "トップ",     "url": f"{self.site_root}index.html"},
                {"name": "商品一覧",   "url": f"{self.site_root}articles/index.html"},
                {"name": cb.name[:20], "url": "#"},
            ],

            # ── 記事ヘッダー ───────────────────────
            "article_title":    cb.build_article_title(),
            "article_subtitle": cb.build_article_subtitle(),
            "category_label":   "楽天おすすめ",
            "published_date":   today.isoformat(),
            "published_date_jp": today.strftime("%Y年%m月%d日"),

            # ── 商品基本情報 ───────────────────────
            "item_name":        item.get("item_name", ""),
            "price":            item.get("price", 0),
            "shop_name":        item.get("shop_name", ""),
            "point_rate":       item.get("point_rate", 1),
            "review_average":   item.get("review_average", 0.0),
            "review_count":     item.get("review_count", 0),
            "image_url":        item.get("image_url", ""),
            "image_alt":        cb.build_image_alt(),
            "affiliate_url":    build_affiliate_url(
                                    item.get("item_url", "#"),
                                    self.affiliate_id,
                                ),

            # ── 記事セクション ─────────────────────
            "intro_paragraphs":  cb.build_intro_paragraphs(),
            "spec_extra_rows":   cb.build_spec_extra_rows(),
            "recommend_points":  cb.build_recommend_points(),
            "target_users":      cb.build_target_users(),
            "not_for_users":     cb.build_not_for_users(),
            "summary_text":      cb.build_summary_text(),

            # ── 関連記事（今回は空） ───────────────
            "related_articles":  [],
        }

    def _build_structured_data(
        self, item: dict[str, Any], cb: ContentBuilder
    ) -> str:
        """
        Google 検索向け Article + Product 構造化データ（JSON-LD）を生成する。
        """
        today = date.today().isoformat()
        data = {
            "@context": "https://schema.org",
            "@type":    "Article",
            "headline": cb.build_article_title(),
            "description": cb.build_meta_description(),
            "image": item.get("image_url", ""),
            "datePublished": today,
            "dateModified":  today,
            "author": {
                "@type": "Organization",
                "name":  self.site_name,
            },
            "publisher": {
                "@type": "Organization",
                "name":  self.site_name,
            },
        }
        return json.dumps(data, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# CLI エントリーポイント（単体実行用）
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys
    import os

    sys.path.insert(0, str(Path(__file__).parent))

    parser = argparse.ArgumentParser(description="商品JSONから記事HTMLを生成します")
    parser.add_argument("--data",     default="data/products.json", help="入力JSONファイル")
    parser.add_argument("--output",   default="articles",           help="出力ディレクトリ")
    parser.add_argument("--templates",default="templates",          help="テンプレートディレクトリ")
    parser.add_argument("--affiliate-id", default=None,             help="楽天アフィリエイトID")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG","INFO","WARNING","ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    affiliate_id = args.affiliate_id or os.environ.get("RAKUTEN_AFFILIATE_ID")

    gen = ArticleGenerator(
        data_path=args.data,
        output_dir=args.output,
        template_dir=args.templates,
        affiliate_id=affiliate_id,
    )

    try:
        results = gen.run()
        print(f"\n✓ {len(results)}件の記事を生成しました")
        for p in results:
            print(f"  → {p}")
    except FileNotFoundError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
