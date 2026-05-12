# podcast-to-youtube

> **個人プロジェクト** — 自分のポッドキャストをYouTubeに載せるために作った自動化ツールです。
> 同じことをやりたい人の参考になればと思い公開しています。

ポッドキャストのRSSフィードから自動でYouTube動画を生成・アップロードするツール。

**Whisper（文字起こし）+ pyannote（話者分離）** で字幕を自動生成し、アートワーク＋音声のMP4動画としてYouTubeに投稿します。

## できること

- RSSフィードから未処理エピソードを自動検出
- Whisper large-v3 による高精度な日本語文字起こし
- pyannote による話者分離（「Aさん：〜」「Bさん：〜」の字幕）
- 話者ごとの字幕カラー設定
- 冒頭ジングルの字幕スキップ
- Whisper誤変換の自動修正（設定ファイルで管理）
- アートワーク静止画 + 音声 → 1920x1080 MP4動画を生成
- YouTube Data API v3 で動画アップロード＋字幕添付
- プレイリストへの自動追加
- macOS launchd による毎日の自動実行

## 処理フロー

```
RSSフィード → 音声ダウンロード → Whisper文字起こし → 話者分離
    → SRT字幕生成 → ffmpegで動画生成 → YouTubeアップロード
```

## 必要なもの

| 項目 | 用途 |
|------|------|
| Python 3.11+ | 実行環境 |
| ffmpeg（libass付き） | 字幕焼き込み動画生成 |
| Google Cloud プロジェクト | YouTube Data API v3 |
| HuggingFace アカウント | pyannote 話者分離モデル |

## セットアップ

### 1. リポジトリをクローン

```bash
git clone https://github.com/yourusername/podcast-to-youtube.git
cd podcast-to-youtube
```

### 2. セットアップスクリプトを実行

```bash
bash setup.sh
```

このスクリプトが以下を自動で行います:
- Python仮想環境の作成
- 依存パッケージのインストール
- ffmpeg-full（libass付き）のインストール確認
- 設定ファイルのテンプレートコピー
- 必要なディレクトリの作成

### 3. ffmpeg-full のインストール（macOS）

字幕を動画に焼き込むために **libass対応のffmpeg** が必要です。

```bash
brew tap homebrew-ffmpeg/ffmpeg
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass
```

> Linux の場合は `sudo apt install ffmpeg libass-dev` でインストールできます。

### 4. YouTube API の準備

1. [Google Cloud Console](https://console.cloud.google.com/) にアクセス
2. 新しいプロジェクトを作成
3. 「YouTube Data API v3」を有効化
4. 「認証情報」→「OAuth 2.0 クライアント ID」を作成（デスクトップアプリ）
5. JSONファイルをダウンロードして `config/credentials/client_secret.json` に配置
6. OAuth同意画面で自分のGoogleアカウントをテストユーザーに追加

```bash
python scripts/auth_local.py
```

ブラウザが開くので、Googleアカウントで認証してください。`token.json` が自動生成されます。

### 5. HuggingFace トークンの取得

pyannote の話者分離モデルを使うために必要です。

1. [HuggingFace](https://huggingface.co/) でアカウント作成
2. [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) の利用規約に同意
3. [アクセストークン](https://huggingface.co/settings/tokens) を発行

```bash
export HF_TOKEN="hf_xxxxx"
```

### 6. 設定ファイルの編集

```bash
nano config/config.yaml
```

最低限変更が必要な項目:
- `rss.feed_url` — あなたのポッドキャストのRSSフィードURL
- `podcast.speakers` — 話者の表示名
- `youtube.description_template` — 説明文テンプレート

詳細は `config/config.yaml.example` を参照してください。

### 7. アートワーク画像の配置

YouTube動画の背景に使う画像を `assets/artwork.jpg` に配置してください。
推奨サイズ: 1920x1080 以上（アスペクト比は自動調整されます）。

## 使い方

### 基本コマンド

```bash
# バッチ処理（config.yamlのbatch_size本を一括処理）
python main.py --mode=batch

# 1本だけ処理
python main.py --mode=single-episode

# 処理本数を指定
python main.py --mode=batch --limit=3

# 特定エピソードを処理
python main.py --mode=single-episode --guid=<GUID>
```

### プレイリストの作成（任意）

```bash
python scripts/create_playlist.py
```

表示されるプレイリストIDを `config.yaml` の `youtube.playlist_id` に設定すると、アップロード後に自動追加されます。

### 毎日の自動実行（macOS）

`scripts/daily_batch.sh` を launchd に登録すると、毎日指定時刻に自動でバッチ処理が実行されます。

```bash
# daily_batch.sh 内のパスを自分の環境に合わせて編集してから:
cp com.podcast-to-youtube.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.podcast-to-youtube.daily.plist
```

## ディレクトリ構成

```
podcast-to-youtube/
├── main.py                        # エントリーポイント
├── setup.sh                       # セットアップスクリプト
├── requirements.txt
├── .env.example                   # 環境変数テンプレート
├── scripts/
│   ├── rss_parser.py              # RSSフィード解析＋進捗管理
│   ├── transcriber.py             # Whisper + pyannote 話者分離
│   ├── subtitle_writer.py         # SRT字幕生成（話者カラー対応）
│   ├── video_builder.py           # ffmpeg MP4生成
│   ├── youtube_uploader.py        # YouTube API アップロード
│   ├── auth_local.py              # OAuth認証ヘルパー
│   ├── create_playlist.py         # プレイリスト作成
│   └── daily_batch.sh             # 自動実行用シェルスクリプト
├── config/
│   ├── config.yaml.example        # 設定テンプレート
│   └── credentials/               # OAuth認証情報（.gitignore済）
├── state/
│   └── progress.json              # 処理状態（自動生成）
├── assets/
│   └── artwork.jpg                # 動画背景画像
└── logs/                          # バッチ実行ログ（自動生成）
```

## 設定リファレンス

### 話者カラー（speaker_colors）

ASS形式 `BBGGRR`（青・緑・赤の順）で指定します。

| 色 | コード | 見え方 |
|----|--------|--------|
| 白 | `FFFFFF` | 標準テキスト |
| 黄色 | `00FFFF` | 目立つアクセント |
| 水色 | `FFFF00` | 爽やかな印象 |
| ピンク | `8080FF` | やわらかい印象 |

### 文字起こし置換ルール（text_replacements）

Whisperが頻繁に間違える固有名詞を自動修正します。

```yaml
text_replacements:
  "金田": "かねだ"      # 人名の漢字→ひらがな
  "給糖質": "給湯室"    # 番組名の誤変換
```

### YouTube API クォータ

- 動画アップロード: 1,600 units/本
- 字幕追加: 50 units/本
- プレイリスト追加: 50 units/本
- デフォルト上限: 10,000 units/日 → **1日最大6本**

## トラブルシューティング

### ffmpegで字幕が焼き込めない

`subtitles` フィルタに libass が必要です。`ffmpeg -filters | grep subtitles` で確認してください。

### Whisperが遅い

CPUでは1エピソード30〜60分かかります。`whisper_model: "medium"` に変更するか、GPU環境（Google Colab等）の使用を検討してください。

### YouTube API で 403 エラー

1日のクォータ上限に達しています。翌日（PST 0:00 = JST 17:00）にリセットされます。

### OAuth トークンが期限切れ

```bash
python scripts/auth_local.py
```

を再実行してトークンをリフレッシュしてください。

## ライセンス

MIT License
