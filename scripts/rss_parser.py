"""
RSSフィード解析・エピソード一覧取得

RSSフィードをパースし、未処理エピソードのリストを返す。
state/progress.json を参照して処理済みエピソードをスキップする。
"""

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

import feedparser

from . import STATE_FILE, load_config

logger = logging.getLogger(__name__)

MAX_RETRIES = 3  # エラーエピソードの最大リトライ回数


def load_progress() -> dict:
    if not STATE_FILE.exists():
        return {"episodes": {}}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(progress: dict) -> None:
    """アトミックにprogress.jsonを保存する（書き込み途中のクラッシュでもデータが壊れない）"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=STATE_FILE.parent, suffix=".tmp", prefix="progress_"
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
        Path(tmp_path).replace(STATE_FILE)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def parse_feed(feed_url: str) -> list[dict]:
    """RSSフィードをパースしてエピソードリストを返す"""
    feed = feedparser.parse(feed_url)
    episodes = []
    for entry in feed.entries:
        audio_url = None
        for enc in getattr(entry, "enclosures", []):
            if enc.get("type", "").startswith("audio/"):
                audio_url = enc.get("href") or enc.get("url")
                break

        if not audio_url:
            continue

        guid = entry.get("id") or entry.get("guid") or entry.get("link", "")
        episodes.append({
            "guid": guid,
            "title": entry.get("title", ""),
            "description": entry.get("summary", entry.get("description", "")),
            "audio_url": audio_url,
            "published_date": entry.get("published", ""),
        })
    return episodes


def get_pending_episodes(limit: int | None = None) -> list[dict]:
    """未処理エピソードのリストを返す（古い順）

    - status="done" のエピソードはスキップ
    - status="error" でリトライ回数が MAX_RETRIES 以上のエピソードもスキップ
    """
    config = load_config()
    progress = load_progress()

    skip_guids = set()
    for guid, info in progress["episodes"].items():
        if info.get("status") == "done":
            skip_guids.add(guid)
        elif info.get("status") == "error":
            if info.get("retry_count", 0) >= MAX_RETRIES:
                skip_guids.add(guid)
                logger.warning("リトライ上限到達でスキップ: %s", guid[:12])

    all_episodes = parse_feed(config["rss"]["feed_url"])
    all_episodes.reverse()

    pending = [ep for ep in all_episodes if ep["guid"] not in skip_guids]
    if limit:
        pending = pending[:limit]
    return pending


def mark_episode(guid: str, status: str, **kwargs) -> None:
    """エピソードのステータスを更新して即時保存する

    - status="done" の場合、過去のerror_msg/retry_countをクリーンアップ
    - status="error" の場合、retry_countをインクリメント
    """
    progress = load_progress()
    ep = progress["episodes"].setdefault(guid, {})
    ep["status"] = status

    if status == "done":
        ep.pop("error_msg", None)
        ep.pop("retry_count", None)
    elif status == "error":
        ep["retry_count"] = ep.get("retry_count", 0) + 1

    for k, v in kwargs.items():
        ep[k] = v

    save_progress(progress)


def check_new_episodes() -> int:
    """新エピソード数を返す"""
    config = load_config()
    progress = load_progress()
    done_guids = {
        guid
        for guid, info in progress["episodes"].items()
        if info.get("status") == "done"
    }
    all_episodes = parse_feed(config["rss"]["feed_url"])
    return sum(1 for ep in all_episodes if ep["guid"] not in done_guids)


def main():
    parser = argparse.ArgumentParser(description="RSSフィード解析")
    parser.add_argument("--check-new", action="store_true",
                        help="新エピソードがあればexit 0、なければexit 1")
    parser.add_argument("--list", action="store_true",
                        help="未処理エピソード一覧を表示")
    args = parser.parse_args()

    if args.check_new:
        count = check_new_episodes()
        print(f"新エピソード数: {count}")
        sys.exit(0 if count > 0 else 1)

    if args.list:
        episodes = get_pending_episodes()
        print(f"未処理エピソード数: {len(episodes)}")
        for ep in episodes:
            print(f"  - [{ep['published_date']}] {ep['title']}")
        return

    episodes = get_pending_episodes()
    print(json.dumps(episodes, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
