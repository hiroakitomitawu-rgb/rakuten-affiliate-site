#!/usr/bin/env python3
"""
check_env.py
GitHub Actions/ローカル実行前の環境変数・パッケージ・ファイル一括チェックスクリプト

使い方:
  python check_env.py

GitHub Actions update.yml への組み込み:
  - name: 事前チェック
    run: python check_env.py
    env:
      RAKUTEN_APPLICATION_ID: ${{ secrets.RAKUTEN_APP_ID }}
      RAKUTEN_AFFILIATE_ID:   ${{ secrets.RAKUTEN_AFFILIATE_ID }}
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Optional

# ── チェック項目定義 ───────────────────────────────────────────────

REQUIRED_ENV_VARS = {
    "RAKUTEN_APPLICATION_ID": {
        "secret_name": "RAKUTEN_APP_ID",
        "url":         "https://webservice.rakuten.co.jp/",
        "required":    True,
    },
    "RAKUTEN_AFFILIATE_ID": {
        "secret_name": "RAKUTEN_AFFILIATE_ID",
        "url":         "https://affiliate.rakuten.co.jp/",
        "required":    False,
    },
}

REQUIRED_PACKAGES = {
    "requests":      {"import_name": "requests", "min_version": "2.28.0"},
    "jinja2":        {"import_name": "jinja2",   "min_version": "3.0.0"},
    "python-dotenv": {"import_name": "dotenv",   "min_version": "0.19.0"},
}

REQUIRED_FILES = [
    "src/api_client.py",
    "src/article_generator.py",
    "src/site_builder.py",
    "src/config.py",
    "templates/base.html",
    "templates/article.html",
    "templates/index.html",
    "fetch_products.py",
    "generate_articles.py",
    "generate_index.py",
    "requirements.txt",
]

# ── ユーティリティ ────────────────────────────────────────────────

SEP = "=" * 55


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except ValueError:
        return (0,)


def _ok(msg: str)   -> None: print("  [OK]  " + msg)
def _warn(msg: str) -> None: print("  [WARN] " + msg)
def _fail(msg: str) -> None: print("  [FAIL] " + msg)
def _head(title: str) -> None:
    print("\n" + SEP)
    print("  " + title)
    print(SEP)


# ── チェック関数 ──────────────────────────────────────────────────

def check_env_vars() -> list:
    _head("(1) 環境変数 / GitHub Secrets チェック")
    errors = []

    for var, meta in REQUIRED_ENV_VARS.items():
        val = os.environ.get(var, "")
        if not val:
            if meta["required"]:
                _fail(var + " が未設定（必須）")
                _fail("     Secret名: " + meta["secret_name"])
                _fail("     取得先 : " + meta["url"])
                errors.append(var)
            else:
                _warn(var + " が未設定（省略可）")
            continue

        if var == "RAKUTEN_APPLICATION_ID":
            if len(val) < 10:
                _fail(var + " の値が短すぎます（10文字以上が正常）")
                errors.append(var)
            elif val in ("your_app_id", "YOUR_APP_ID", "TEST_APP_ID"):
                _fail(var + " にテスト値が設定されています: " + val)
                errors.append(var)
            else:
                masked = val[:4] + "****" + val[-4:] if len(val) >= 8 else "****"
                _ok(var + " 設定済み: " + masked + " (" + str(len(val)) + "文字)")
        else:
            _ok(var + " 設定済み: " + val[:4] + "****")

    return errors


def check_packages() -> list:
    _head("(2) Python パッケージ チェック")
    errors = []

    for pkg, meta in REQUIRED_PACKAGES.items():
        try:
            try:
                installed = pkg_version(pkg)
            except PackageNotFoundError:
                mod = importlib.import_module(meta["import_name"])
                installed = getattr(mod, "__version__", "0.0.0")

            min_v = meta["min_version"]
            if _ver(installed) >= _ver(min_v):
                _ok(pkg + " " + installed + " (>=" + min_v + ")")
            else:
                _fail(pkg + " " + installed + " が古い（" + min_v + " 以上が必要）")
                _fail("     -> pip install --upgrade " + pkg)
                errors.append(pkg)
        except ImportError:
            _fail(pkg + " が未インストール")
            _fail("     -> pip install " + pkg)
            errors.append(pkg)

    return errors


def check_files() -> list:
    _head("(3) 必須ファイル 存在チェック")
    errors = []

    for fp in REQUIRED_FILES:
        p = Path(fp)
        if p.exists():
            size = p.stat().st_size / 1024
            _ok(fp + " ({:.1f}KB)".format(size))
        else:
            _fail(fp + " が見つかりません")
            errors.append(fp)

    return errors


def check_api_connectivity() -> list:
    _head("(4) 楽天API 疎通確認")
    errors = []

    app_id = os.environ.get("RAKUTEN_APPLICATION_ID", "")
    placeholders = ("", "your_app_id", "YOUR_APP_ID", "TEST_APP_ID_12345", "TEST")
    if app_id in placeholders:
        _warn("RAKUTEN_APPLICATION_ID が未設定のため疎通確認をスキップします")
        return errors

    try:
        import requests
        endpoint = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"
        params = "?applicationId=" + app_id + "&keyword=test&hits=1&format=json"
        resp = requests.get(endpoint + params, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            if "error" in data:
                _fail("APIエラー: " + str(data.get("error")) + " / " + str(data.get("error_description", "")))
                _fail("     -> RAKUTEN_APP_ID の値を確認してください")
                errors.append("API_ERROR")
            else:
                _ok("楽天API 接続成功 (HTTP 200)")
                _ok("レスポンス正常: " + str(data.get("count", "?")) + " 件ヒット")

        elif resp.status_code == 400:
            _fail("HTTP 400 - アプリIDが無効の可能性があります")
            errors.append("HTTP_400")
        elif resp.status_code == 429:
            _warn("HTTP 429 - レート制限中（しばらく待ってから再試行）")
        else:
            _fail("HTTP " + str(resp.status_code) + " - 予期しないステータス")
            errors.append("HTTP_" + str(resp.status_code))

    except Exception as exc:
        _fail("接続エラー: " + str(exc))
        errors.append("CONNECTION_ERROR")

    return errors


def check_data_dir() -> list:
    _head("(5) データ・ディレクトリ チェック")
    errors = []

    Path("data").mkdir(exist_ok=True)
    _ok("data/ ディレクトリ確認済み")

    pj = Path("data/products.json")
    if pj.exists():
        try:
            d = json.loads(pj.read_text(encoding="utf-8"))
            count = d.get("count", len(d.get("products", [])))
            _ok("data/products.json: " + str(count) + " 件の商品データ")
        except Exception as exc:
            _fail("data/products.json 読み込みエラー: " + str(exc))
            errors.append("products_json_broken")
    else:
        _warn("data/products.json 未作成 - fetch_products.py を先に実行してください")

    for d in ["docs", "docs/articles", "templates"]:
        Path(d).mkdir(parents=True, exist_ok=True)
    _ok("docs/ docs/articles/ templates/ 確認済み")

    return errors


# ── メイン ────────────────────────────────────────────────────────

def main() -> int:
    print("\n" + "=" * 55)
    print("  楽天アフィリエイトサイト - 環境チェック")
    print("=" * 55)

    all_errors: list = []
    all_errors += check_env_vars()
    all_errors += check_packages()
    all_errors += check_files()
    all_errors += check_api_connectivity()
    all_errors += check_data_dir()

    print("\n" + "=" * 55)
    if not all_errors:
        print("  [OK] 全チェック通過！実行準備が整っています。")
        print()
        print("  次のステップ:")
        print("    1. python fetch_products.py")
        print("    2. python generate_articles.py")
        print("    3. python generate_index.py")
        print("    4. git add -A && git commit && git push")
        exit_code = 0
    else:
        print("  [FAIL] " + str(len(all_errors)) + " 件の問題が見つかりました。")
        print()
        print("  修正が必要な項目:")
        for e in all_errors:
            print("    - " + str(e))
        print()
        print("  詳細は上記のチェック結果を参照してください。")

        summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
        if summary_path:
            try:
                with open(summary_path, "a", encoding="utf-8") as sf:
                    sf.write("## 環境チェック失敗\n\n")
                    sf.write("修正が必要な項目:\n\n")
                    for e in all_errors:
                        sf.write("- " + str(e) + "\n")
                    sf.write("\n### よくある原因\n")
                    sf.write("- RAKUTEN_APP_ID が未設定\n")
                    sf.write("- pip install -r requirements.txt が未実行\n")
                    sf.write("- Workflow permissions が Read-only になっている\n")
            except OSError:
                pass

        exit_code = 1

    print("=" * 55 + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
