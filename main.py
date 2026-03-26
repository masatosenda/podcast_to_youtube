"""
メインエントリーポイント

使い方:
  python main.py --mode=batch          # バッチ処理（batch_size本）
  python main.py --mode=single-episode # 最新未処理1本のみ処理（GitHub Actions用）
  python main.py --mode=single-episode --guid=<GUID>  # 特定エピソードを処理
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

import requests
import yaml
from googleapiclient.errors import HttpError

ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from rss_parser import get_pending_episodes, mark_episode
from transcriber import transcribe_and_diarize
from subtitle_writer import write_srt
from video_builder import build_video
from youtube_uploader import upload_episode

CONFIG_FILE = ROOT_DIR / "config" / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def download_audio(url: str, dest_path: str) -> None:
    """音声ファイルをダウンロードする"""
    print(f"[main] 音声ダウンロード: {url}")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    print(f"[main] ダウンロード完了: {dest_path}")


def process_episode(episode: dict, config: dict) -> str:
    """
    1エピソードを処理してYouTubeにアップロードする。

    Returns:
        YouTube video ID
    """
    guid = episode["guid"]
    title = episode["title"]
    description = episode["description"]
    audio_url = episode["audio_url"]

    processing = config.get("processing", {})
    artwork_path = str(ROOT_DIR / config["podcast"]["artwork_path"])
    speaker_map = config.get("podcast", {}).get("speakers", {})

    with tempfile.TemporaryDirectory() as tmpdir:
        # 音声ファイル名の拡張子をURLから推測
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
        )

        # 3. SRT生成
        write_srt(segments, srt_path)

        # 4. MP4生成
        build_video(artwork_path, audio_path, video_path)

        # 5. YouTubeアップロード
        video_id = upload_episode(
            video_path=video_path,
            srt_path=srt_path,
            title=title,
            description=description,
            config=config,
        )

    return video_id


def run_batch(limit: int | None = None) -> None:
    """バッチ処理: limit本（Noneの場合はbatch_size）を処理する"""
    config = load_config()
    batch_size = limit or config.get("processing", {}).get("batch_size", 25)
    episodes = get_pending_episodes(limit=batch_size)

    if not episodes:
        print("[main] 未処理エピソードはありません。")
        return

    print(f"[main] {len(episodes)}本を処理します...")

    for i, episode in enumerate(episodes, start=1):
        guid = episode["guid"]
        print(f"\n[main] [{i}/{len(episodes)}] {episode['title']}")
        mark_episode(guid, "pending")
        try:
            video_id = process_episode(episode, config)
            mark_episode(guid, "done", youtube_id=video_id)
            print(f"[main] 完了: {episode['title']} → https://youtu.be/{video_id}")
        except HttpError as e:
            if e.resp.status == 403:
                mark_episode(guid, "error", error_msg="quota exceeded")
                print("[main] クォータ超過のため処理を中断します。")
                sys.exit(1)
            mark_episode(guid, "error", error_msg=str(e))
            print(f"[main] エラー (HTTPError): {e}")
        except Exception as e:
            mark_episode(guid, "error", error_msg=str(e))
            print(f"[main] エラー: {e}")
            # バッチ処理では1本エラーでも継続


def run_single(guid: str | None = None) -> None:
    """単体処理: 最新未処理1本（またはGUID指定）を処理する"""
    config = load_config()

    if guid:
        # GUIDが指定された場合は強制処理
        from rss_parser import parse_feed
        all_episodes = parse_feed(config["rss"]["feed_url"])
        episodes = [ep for ep in all_episodes if ep["guid"] == guid]
        if not episodes:
            print(f"[main] GUIDが見つかりません: {guid}")
            sys.exit(1)
    else:
        episodes = get_pending_episodes(limit=1)

    if not episodes:
        print("[main] 未処理エピソードはありません。")
        sys.exit(0)

    episode = episodes[0]
    guid = episode["guid"]
    print(f"[main] 処理開始: {episode['title']}")
    mark_episode(guid, "pending")
    try:
        video_id = process_episode(episode, config)
        mark_episode(guid, "done", youtube_id=video_id)
        print(f"[main] 完了: {episode['title']} → https://youtu.be/{video_id}")
    except HttpError as e:
        if e.resp.status == 403:
            mark_episode(guid, "error", error_msg="quota exceeded")
            print("[main] クォータ超過のため処理を中断します。")
        else:
            mark_episode(guid, "error", error_msg=str(e))
        sys.exit(1)
    except Exception as e:
        mark_episode(guid, "error", error_msg=str(e))
        print(f"[main] エラー: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Podcast → YouTube パイプライン")
    parser.add_argument(
        "--mode",
        choices=["batch", "single-episode"],
        default="batch",
        help="実行モード (batch: バッチ処理, single-episode: 1本処理)",
    )
    parser.add_argument(
        "--guid",
        default=None,
        help="処理対象エピソードのGUID（single-episodeモード時のみ有効）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="処理本数の上限（batchモード時のみ有効、未指定時はconfig.batch_sizeを使用）",
    )
    args = parser.parse_args()

    if args.mode == "batch":
        run_batch(limit=args.limit)
    elif args.mode == "single-episode":
        run_single(guid=args.guid)


if __name__ == "__main__":
    main()
