#!/bin/bash
# 毎日の自動バッチ処理スクリプト
# macOS launchd から呼び出される想定
#
# 事前準備:
#   1. .env ファイルをプロジェクトルートに作成（.env.example を参照）
#   2. launchd plist を ~/Library/LaunchAgents/ に配置

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/batch_$(date +%Y%m%d_%H%M%S).log"

# ログディレクトリ作成
mkdir -p "$LOG_DIR"

echo "=== バッチ処理開始: $(date) ===" | tee "$LOG_FILE"

# .env ファイルから環境変数を読み込み
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
else
    echo "ERROR: .env ファイルが見つかりません。.env.example を参考に作成してください。" | tee -a "$LOG_FILE"
    exit 1
fi

# Python パス（.env の PYTHON_PATH、未設定なら python3 を使用）
PYTHON="${PYTHON_PATH:-python3}"

# ffmpeg-full（libass対応）がある場合はPATHに追加
if [ -d "/opt/homebrew/opt/ffmpeg-full/bin" ]; then
    export PATH="/opt/homebrew/opt/ffmpeg-full/bin:$PATH"
fi

cd "$PROJECT_DIR"

# バッチ実行（config.yamlのbatch_size本を処理）
# caffeinate -s: 処理中はスリープを防止（処理完了後に自動解除）
caffeinate -s "$PYTHON" main.py --mode=batch 2>&1 | tee -a "$LOG_FILE"

echo "=== バッチ処理完了: $(date) ===" | tee -a "$LOG_FILE"

# 古いログを削除（30日以上前）
find "$LOG_DIR" -name "batch_*.log" -mtime +30 -delete 2>/dev/null || true
