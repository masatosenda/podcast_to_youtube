"""
メインエントリーポイント

使い方:
  python main.py --mode=batch          # バッチ処理（batch_size本）
  python main.py --mode=single-episode # 最新未処理1本のみ処理
  python main.py --mode=single-episode --guid=<GUID>  # 特定エピソードを処理
"""

import argparse
import html
import logging
import os
import re
import sys
import tempfile
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from googleapiclient.errors import HttpError

# scripts/ をパッケージとしてインポートするためのパス設定
# （pyproject.toml でのパッケージ化は将来課題）
ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))

from scripts import load_config, setup_logging
from scripts.rss_parser import get_pending_episodes, load_progress, mark_episode, parse_feed
from scripts.transcriber import transcribe_and_diarize
from scripts.subtitle_writer import write_srt
from scripts.video_builder import build_video
from scripts.youtube_uploader import upload_episode, add_to_playlist

logger = logging.getLogger(__name__)


def download_audio(url: str, dest_path: str) -> None:
    """音声ファイルをダウンロードする"""
    logger.info("音声ダウンロード: %s", url)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    logger.info("ダウンロード完了: %s", dest_path)


def format_title(raw_title: str, published_date: str) -> str:
    """タイトルをYouTube向けにフォーマットする。"""
    title = re.sub(r"^(#\d+)([^\s\d])", r"\1 \2", raw_title)

    try:
        dt = parsedate_to_datetime(published_date)
        date_str = f"{dt.year}/{dt.month}/{dt.day}"
        title = f"{title} {date_str}"
    except Exception:
        pass

    return title


def _strip_html(text: str) -> str:
    """HTMLタグを除去し、エンティティをデコードする。"""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def format_description(original_description: str, published_date: str, config: dict) -> str:
    """YouTube用の説明文を組み立てる。"""
    clean_description = _strip_html(original_description)

    try:
        dt = parsedate_to_datetime(published_date)
        date_str = f"{dt.year}/{dt.month}/{dt.day}"
        date_line = f"Spotify 配信日: {date_str}"
    except Exception:
        date_line = ""

    desc_template = config.get("youtube", {}).get("description_template", "")
    if desc_template:
        description = desc_template.format(
            date_line=date_line,
            original_description=clean_description,
        )
    else:
        parts = [f"🎙 {date_line}", "", clean_description]
        description = "\n".join(parts)

    if len(description) > 5000:
        description = description[:4997] + "..."

    return description


def _is_already_uploaded(guid: str) -> str | None:
    """既にアップロード済みの場合、youtube_idを返す（重複アップロード防止）"""
    progress = load_progress()
    ep = progress.get("episodes", {}).get(guid, {})
    if ep.get("youtube_id"):
        return ep["youtube_id"]
    return None


def process_episode(episode: dict, config: dict) -> str:
    """1エピソードを処理してYouTubeにアップロードする。"""
    guid = episode["guid"]
    title = format_title(episode["title"], episode.get("published_date", ""))
    description = format_description(
        episode["description"], episode.get("published_date", ""), config
    )
    audio_url = episode["audio_url"]

    # 重複アップロード防止: 前回アップロード成功→字幕失敗のケース
    existing_id = _is_already_uploaded(guid)
    if existing_id:
        logger.warning("既にアップロード済み (youtube_id=%s)。スキップします。", existing_id)
        return existing_id

    processing = config.get("processing", {})
    artwork_path = str(ROOT_DIR / config["podcast"]["artwork_path"])
    speaker_map = config.get("podcast", {}).get("speakers", {})
    text_replacements = config.get("podcast", {}).get("text_replacements", {})
    speaker_colors = config.get("podcast", {}).get("speaker_colors", {})

    with tempfile.TemporaryDirectory() as tmpdir:
        ext = Path(audio_url.split("?")[0]).suffix or ".mp3"
        audio_path = os.path.join(tmpdir, f"audio{ext}")
        video_path = os.path.join(tmpdir, "output.mp4")
        srt_path = os.path.join(tmpdir, "subtitle.srt")

        # 1. 音声ダウンロード
        download_audio(audio_url, audio_path)

        # 2. 文字起こし＋話者分離
        segments = transcribe_and_diarize(
            audio_path=audio_path,
            whisper_model=processing.get("whisper_model", "large-v3"),
            language=processing.get("language", "ja"),
            speaker_map=speaker_map,
            text_replacements=text_replacements,
        )

        # 3. SRT生成
        jingle_skip = config.get("podcast", {}).get("jingle_skip_seconds", 0)
        write_srt(segments, srt_path, speaker_colors=speaker_colors, jingle_skip_seconds=jingle_skip)

        # 4. MP4生成
        build_video(artwork_path, audio_path, video_path, srt_path=srt_path)

        # 5. YouTubeアップロード
        video_id = upload_episode(
            video_path=video_path,
            srt_path=srt_path,
            title=title,
            description=description,
            config=config,
        )

        # 6. プレイリストに追加
        playlist_id = config.get("youtube", {}).get("playlist_id")
        if playlist_id:
            try:
                add_to_playlist(video_id, playlist_id)
            except Exception as e:
                logger.warning("プレイリスト追加失敗（動画アップは成功）: %s", e)

    return video_id


def run_batch(limit: int | None = None) -> None:
    """バッチ処理: limit本（Noneの場合はbatch_size）を処理する"""
    config = load_config()
    batch_size = limit or config.get("processing", {}).get("batch_size", 25)
    episodes = get_pending_episodes(limit=batch_size)

    if not episodes:
        logger.info("未処理エピソードはありません。")
        return

    logger.info("%d本を処理します...", len(episodes))

    for i, episode in enumerate(episodes, start=1):
        guid = episode["guid"]
        logger.info("[%d/%d] %s", i, len(episodes), episode["title"])
        mark_episode(guid, "pending")
        try:
            video_id = process_episode(episode, config)
            mark_episode(guid, "done", youtube_id=video_id)
            logger.info("完了: %s → https://youtu.be/%s", episode["title"], video_id)
        except HttpError as e:
            if e.resp.status == 403:
                mark_episode(guid, "error", error_msg="quota exceeded")
                logger.error("クォータ超過のため処理を中断します。")
                sys.exit(1)
            mark_episode(guid, "error", error_msg=str(e))
            logger.error("HTTPError: %s", e)
        except Exception as e:
            mark_episode(guid, "error", error_msg=str(e))
            logger.error("エラー: %s", e)


def run_single(guid: str | None = None) -> None:
    """単体処理: 最新未処理1本（またはGUID指定）を処理する"""
    config = load_config()

    if guid:
        all_episodes = parse_feed(config["rss"]["feed_url"])
        episodes = [ep for ep in all_episodes if ep["guid"] == guid]
        if not episodes:
            logger.error("GUIDが見つかりません: %s", guid)
            sys.exit(1)
    else:
        episodes = get_pending_episodes(limit=1)

    if not episodes:
        logger.info("未処理エピソードはありません。")
        sys.exit(0)

    episode = episodes[0]
    guid = episode["guid"]
    logger.info("処理開始: %s", episode["title"])
    mark_episode(guid, "pending")
    try:
        video_id = process_episode(episode, config)
        mark_episode(guid, "done", youtube_id=video_id)
        logger.info("完了: %s → https://youtu.be/%s", episode["title"], video_id)
    except HttpError as e:
        if e.resp.status == 403:
            mark_episode(guid, "error", error_msg="quota exceeded")
            logger.error("クォータ超過のため処理を中断します。")
        else:
            mark_episode(guid, "error", error_msg=str(e))
        sys.exit(1)
    except Exception as e:
        mark_episode(guid, "error", error_msg=str(e))
        logger.error("エラー: %s", e)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Podcast → YouTube パイプライン")
    parser.add_argument(
        "--mode",
        choices=["batch", "single-episode"],
        default="batch",
    )
    parser.add_argument("--guid", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    setup_logging()

    if args.mode == "batch":
        run_batch(limit=args.limit)
    elif args.mode == "single-episode":
        run_single(guid=args.guid)


if __name__ == "__main__":
    main()
