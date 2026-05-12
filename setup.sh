#!/bin/bash
# podcast-to-youtube セットアップスクリプト
set -euo pipefail

echo "=== podcast-to-youtube セットアップ ==="
echo ""

# 1. Python仮想環境
if [ ! -d ".venv" ]; then
    echo "[1/5] Python仮想環境を作成中..."
    python3 -m venv .venv
    echo "  → .venv/ 作成完了"
else
    echo "[1/5] Python仮想環境は既に存在します"
fi

echo "[2/5] 依存パッケージをインストール中..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
echo "  → インストール完了"

# 3. ffmpeg確認
echo "[3/5] ffmpegを確認中..."
if command -v ffmpeg &>/dev/null; then
    if ffmpeg -filters 2>/dev/null | grep -q subtitles; then
        echo "  → ffmpeg (libass対応) が見つかりました"
    else
        echo "  ⚠ ffmpegにlibassが含まれていません。字幕焼き込みに必要です。"
        echo "    macOS: brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass"
        echo "    Linux: sudo apt install ffmpeg libass-dev"
    fi
else
    echo "  ⚠ ffmpegが見つかりません。インストールしてください。"
    echo "    macOS: brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass"
    echo "    Linux: sudo apt install ffmpeg libass-dev"
fi

# 4. 設定ファイル
echo "[4/5] 設定ファイルを準備中..."
if [ ! -f "config/config.yaml" ]; then
    cp config/config.yaml.example config/config.yaml
    echo "  → config/config.yaml を作成しました（テンプレートからコピー）"
    echo "  → 必ず編集してください: nano config/config.yaml"
else
    echo "  → config/config.yaml は既に存在します"
fi

# 5. ディレクトリ作成
echo "[5/5] ディレクトリを作成中..."
mkdir -p config/credentials state logs assets
touch config/credentials/.gitkeep state/.gitkeep
echo "  → config/credentials/, state/, logs/, assets/ を作成しました"

echo ""
echo "=== セットアップ完了 ==="
echo ""
echo "次のステップ:"
echo "  1. config/config.yaml を編集（RSSフィードURL、話者名など）"
echo "  2. YouTube API の OAuth 認証情報を設定:"
echo "     → client_secret.json を config/credentials/ に配置"
echo "     → .venv/bin/python scripts/auth_local.py を実行"
echo "  3. HuggingFace トークンを設定:"
echo "     → export HF_TOKEN=\"hf_xxxxx\""
echo "  4. アートワーク画像を assets/artwork.jpg に配置"
echo "  5. 実行:"
echo "     → .venv/bin/python main.py --mode=single-episode"
echo ""
