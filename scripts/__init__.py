"""podcast-to-youtube スクリプトパッケージ"""

import logging
import os
import sys
from pathlib import Path

import yaml

# プロジェクトルートの解決
ROOT_DIR = Path(__file__).parent.parent
CONFIG_FILE = ROOT_DIR / "config" / "config.yaml"
STATE_FILE = ROOT_DIR / "state" / "progress.json"
CREDENTIALS_DIR = ROOT_DIR / "config" / "credentials"


def load_config() -> dict:
    """設定ファイルを読み込む（プロジェクト全体で共通）"""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging() -> None:
    """ログ設定を初期化する"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # 外部ライブラリのログレベルを抑制
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
