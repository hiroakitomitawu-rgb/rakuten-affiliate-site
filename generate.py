#!/usr/bin/env python3
"""
generate.py
楽天アフィリエイトサイト自動生成 CLI エントリーポイント

使い方:
  # キーワード検索（デフォルト: レビュー評価順・30件）
  python generate.py --keyword "コーヒーメーカー"

  # ジャンル + キーワード + ソート指定
  python generate.py --keyword "プロテイン" --genre health --sort review_count

  # ジャンルIDを直接指定（楽天ジャンルIDの数字）
  python generate.py --genre 100316 --sort affiliation --hits 20

  # .env ファイルから環境変数を読み込む（ローカル開発用）
  python generate.py --keyword "ヨガマット" --env .env

  # 保存ファイル名を明示指定
  python generate.py --keyword "テント" --output camping_20240101.json
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# python-dotenv が未インストールでも動作するよう条件インポート
try:
    from dotenv import load_dotenv
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False

# src/ ディレクトリを import パスに追加（プロジェクトルートから実行する場合）
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import AppConfig, GENRE_MAP, SORT_MAP, MAX_HITS
from api_client import RakutenAPIClient


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパースする。"""
    parser = argparse.ArgumentParser(
        prog="generate.py",
        description="楽天商品APIから商品データを取得してJSONに保存します",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
ジャンルキー一覧:
  {", ".join(sorted(GENRE_MAP.keys()))}

ソートキー一覧:
  {", ".join(sorted(SORT_MAP.keys()))}

例:
  python generate.py --keyword "ワイヤレスイヤホン" --genre smartphone --sort review_avg
  python generate.py --genre food --sort affiliation --hits 30
        """,
    )

    parser.add_argument(
        "--keyword", "-k",
        type=str,
        default=None,
        help="検索キーワード（省略可能。ジャンル全体を取得する場合は省略）",
    )
    parser.add_argument(
        "--genre", "-g",
        type=str,
        default="all",
        help=(
            "ジャンルキー（例: food, health, pc）または楽天ジャンルID（数字）。"
            f"デフォルト: all（全ジャンル）"
        ),
    )
    parser.add_argument(
        "--sort", "-s",
        type=str,
        default="standard",
        choices=list(SORT_MAP.keys()),
        help="ソート順。デフォルト: standard（標準）",
    )
    parser.add_argument(
        "--hits", "-n",
        type=int,
        default=MAX_HITS,
        metavar=f"1-{MAX_HITS}",
        help=f"取得件数（1〜{MAX_HITS}）。デフォルト: {MAX_HITS}",
    )
    parser.add_argument(
        "--page", "-p",
        type=int,
        default=1,
        help="取得ページ番号（1始まり）。デフォルト: 1",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        metavar="FILENAME",
        help=(
            "保存ファイル名（data/ ディレクトリ以下）。"
            "省略時は products_YYYYMMDD.json を自動生成。"
        ),
    )
    parser.add_argument(
        "--env",
        type=str,
        default=None,
        metavar=".env PATH",
        help=".env ファイルパス（省略時は環境変数を直接参照）",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル。デフォルト: INFO",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="APIリクエストと保存をスキップし、パラメータ確認のみ行う",
    )

    return parser.parse_args()


def setup_logging(level: str) -> None:
    """ロギングを初期化する。"""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_env_file(env_path: str) -> None:
    """
    .env ファイルから環境変数を読み込む。
    python-dotenv が未インストールの場合は警告を表示して続行する。
    """
    if not _DOTENV_AVAILABLE:
        logging.warning(
            "--env オプションが指定されましたが python-dotenv が未インストールです。\n"
            "  pip install python-dotenv でインストールしてください。\n"
            "  環境変数を直接設定して実行を続けます。"
        )
        return

    env_file = Path(env_path)
    if not env_file.exists():
        logging.error(".env ファイルが見つかりません: %s", env_path)
        sys.exit(1)

    load_dotenv(env_file, override=True)
    logging.info(".env ファイルを読み込みました: %s", env_path)


def validate_args(args: argparse.Namespace) -> None:
    """引数の値を検証する。"""
    if not (1 <= args.hits <= MAX_HITS):
        print(f"エラー: --hits は 1〜{MAX_HITS} の範囲で指定してください。", file=sys.stderr)
        sys.exit(1)
    if args.page < 1:
        print("エラー: --page は 1 以上の整数で指定してください。", file=sys.stderr)
        sys.exit(1)
    if not args.keyword and args.genre == "all":
        print(
            "警告: --keyword も --genre も指定されていません。"
            " 全ジャンルの標準順で取得します。",
            file=sys.stderr,
        )


def main() -> int:
    """
    エントリーポイント。

    Returns
    -------
    int
        終了コード（0: 成功, 1: エラー）
    """
    args = parse_args()
    setup_logging(args.log_level)
    logger = logging.getLogger("generate")

    # .env ファイルの読み込み
    if args.env:
        load_env_file(args.env)

    # 引数バリデーション
    validate_args(args)

    # ドライランモード: パラメータを表示して終了
    if args.dry_run:
        print("=== Dry Run モード ===")
        print(f"  keyword : {args.keyword or '(なし)'}")
        print(f"  genre   : {args.genre}")
        print(f"  sort    : {args.sort}")
        print(f"  hits    : {args.hits}")
        print(f"  page    : {args.page}")
        print(f"  output  : {args.output or '(自動生成: products_YYYYMMDD.json)'}")
        return 0

    # AppConfig（環境変数読み込み）
    try:
        config = AppConfig()
    except EnvironmentError as e:
        logger.error("%s", e)
        return 1

    # APIクライアント実行
    try:
        with RakutenAPIClient(config) as client:
            products = client.search(
                keyword=args.keyword,
                genre=args.genre,
                sort=args.sort,
                hits=args.hits,
                page=args.page,
            )

            if not products:
                logger.warning("商品が1件も取得できませんでした。条件を変えてお試しください。")
                return 0

            saved_path = client.save(products, filename=args.output)

        print(f"\n✓ {len(products)}件の商品データを保存しました")
        print(f"  → {saved_path}")
        return 0

    except ValueError as e:
        # ジャンルキー・ソートキーの不正など
        logger.error("引数エラー: %s", e)
        return 1

    except RuntimeError as e:
        # API エラー・リトライ超過など
        logger.error("実行エラー: %s", e)
        return 1

    except KeyboardInterrupt:
        logger.info("ユーザーによって中断されました。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
