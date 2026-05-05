"""
config.py
設定値・環境変数・楽天ジャンルIDを一元管理するモジュール
"""
import os
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
# 楽天 API エンドポイント
# ─────────────────────────────────────────────
RAKUTEN_API_BASE_URL = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"

# API バージョン
RAKUTEN_API_VERSION = "20220601"

# 1リクエストあたりの最大取得件数（楽天API上限: 30）
MAX_HITS = 30

# リクエスト間の待機秒数（レート制限対策: 楽天は1秒1リクエスト推奨）
REQUEST_INTERVAL_SEC = 1.0

# リトライ設定
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 2.0   # リトライごとに exponential backoff

# タイムアウト設定（秒）
REQUEST_TIMEOUT_SEC = 10

# データ保存先ディレクトリ
DATA_DIR = "data"

# ─────────────────────────────────────────────
# 楽天ジャンルID マッピング（主要カテゴリ）
# https://webservice.rakuten.co.jp/explorer/api/IchibaGenre/Search/
# ─────────────────────────────────────────────
GENRE_MAP: dict[str, str] = {
    "all":           "0",        # 全ジャンル
    "lady_fashion":  "100371",   # レディースファッション
    "men_fashion":   "551177",   # メンズファッション
    "shoes":         "100533",   # シューズ
    "bag":           "100534",   # バッグ・小物・ブランド雑貨
    "jewelry":       "216131",   # ジュエリー・アクセサリー
    "beauty":        "216129",   # コスメ・香水・美容
    "health":        "100227",   # ダイエット・健康
    "food":          "500322",   # 食品・グルメ
    "sweets":        "500322",   # スイーツ・お菓子
    "gourmet":       "100316",   # グルメ・おつまみ
    "kitchen":       "100804",   # キッチン・日用品・その他
    "furniture":     "100804",   # インテリア・寝具・収納
    "interior":      "100804",   # インテリア・寝具
    "diy":           "101164",   # DIY・工具
    "sports":        "101070",   # スポーツ・アウトドア
    "golf":          "101117",   # ゴルフ
    "toy":           "101280",   # おもちゃ・ゲーム
    "hobby":         "101113",   # ホビー
    "book":          "200162",   # 本・雑誌・コミック
    "music":         "101726",   # CD・DVD
    "game":          "101229",   # テレビゲーム
    "pc":            "100026",   # パソコン・周辺機器
    "smartphone":    "101234",   # スマートフォン・タブレット
    "camera":        "101068",   # カメラ・ビデオカメラ
    "appliance":     "100026",   # 家電・カメラ
    "car":           "101087",   # カー用品・バイク用品
    "pet":           "101371",   # ペット・ペット用品
    "baby":          "100006",   # ベビー・キッズ・マタニティ
    "travel":        "101027",   # 旅行・出張用品
    "flower":        "100039",   # フラワー・ガーデン・DIY
}


# ─────────────────────────────────────────────
# ソート順マッピング
# ─────────────────────────────────────────────
SORT_MAP: dict[str, str] = {
    "standard":       "standard",        # 標準
    "affiliation":    "-affiliateRate",  # アフィリエイト料率が高い順
    "review_count":   "-reviewCount",    # レビュー件数が多い順
    "review_avg":     "-reviewAverage",  # レビュー評価が高い順
    "price_asc":      "+itemPrice",      # 価格が安い順
    "price_desc":     "-itemPrice",      # 価格が高い順
    "update":         "-updateTimestamp",# 更新日時が新しい順
}


# ─────────────────────────────────────────────
# 環境変数から読み込む設定
# ─────────────────────────────────────────────
@dataclass
class AppConfig:
    """アプリケーション設定。環境変数から初期化する。"""

    # 楽天アプリID（必須）
    application_id: str = field(
        default_factory=lambda: _require_env("RAKUTEN_APPLICATION_ID")
    )

    # 楽天アフィリエイトID（省略可能）
    affiliate_id: Optional[str] = field(
        default_factory=lambda: os.environ.get("RAKUTEN_AFFILIATE_ID")
    )

    # データ保存先ディレクトリ（環境変数で上書き可能）
    data_dir: str = field(
        default_factory=lambda: os.environ.get("DATA_DIR", DATA_DIR)
    )

    # ログレベル
    log_level: str = field(
        default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO")
    )


def _require_env(key: str) -> str:
    """環境変数が未設定の場合は EnvironmentError を送出する。"""
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(
            f"必須の環境変数 '{key}' が設定されていません。\n"
            f"  export {key}=<あなたのアプリID>  を実行してください。\n"
            f"  楽天アプリIDの取得: https://webservice.rakuten.co.jp/"
        )
    return value


def get_genre_id(genre_key: str) -> str:
    """
    ジャンルキー（英語エイリアス）またはジャンルID（数字文字列）を受け取り、
    楽天APIで使用するジャンルID文字列を返す。

    Parameters
    ----------
    genre_key : str
        GENRE_MAP のキー名（例: "food"）または数字のジャンルID（例: "100316"）

    Returns
    -------
    str
        楽天APIに渡すジャンルID文字列
    """
    if genre_key.isdigit():
        return genre_key
    genre_id = GENRE_MAP.get(genre_key)
    if genre_id is None:
        available = ", ".join(sorted(GENRE_MAP.keys()))
        raise ValueError(
            f"不明なジャンルキー: '{genre_key}'\n"
            f"使用可能なキー: {available}"
        )
    return genre_id


def get_sort_key(sort_key: str) -> str:
    """
    ソートキー名を楽天API用のソートパラメータ文字列に変換する。

    Parameters
    ----------
    sort_key : str
        SORT_MAP のキー名（例: "review_avg"）

    Returns
    -------
    str
        楽天APIに渡すソート文字列
    """
    sort_param = SORT_MAP.get(sort_key)
    if sort_param is None:
        available = ", ".join(sorted(SORT_MAP.keys()))
        raise ValueError(
            f"不明なソートキー: '{sort_key}'\n"
            f"使用可能なキー: {available}"
        )
    return sort_param
