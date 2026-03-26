"""
RSSフィード解析・エピソード一覧取得

RSSフィードをパースし、未処理エピソードのリストを返す。
state/progress.json を参照して処理済みエピソードをスキップする。
"""

import argparse
import json
import sys
from pathlib import Path

import feedparser
import yaml

ROOT_DIR = Path(__file__).parent.parent
STATE_FILE = ROOT_DIR / "state" / "progress.json"
CONFIG_FILE = ROOT_DIR / "config" / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_progress() -> dict:
    if not STATE_FILE.exists():
        return {"episodes": {}}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(progress: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def parse_feed(feed_url: str) -> list[dict]:
    """RSSフィードをパースしてエピソードリストを返す"""
    feed = feedparser.parse(feed_url)
    episodes = []
    for entry in feed.entries:
        # enclosureタグから音声URLを取得
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
    """未処理エピソードのリストを返す（古い順）"""
    config = load_config()
    progress = load_progress()
    done_guids = {
        guid
        for guid, info in progress["episodes"].items()
        if info.get("status") == "done"
    }

    all_episodes = parse_feed(config["rss"]["feed_url"])
    # RSSは新しい順なので逆順にして古い順で処理
    all_episodes.reverse()

    pending = [ep for ep in all_episodes if ep["guid"] not in done_guids]
    if limit:
        pending = pending[:limit]
    return pending


def mark_episode(guid: str, status: str, **kwargs) -> None:
    """エピソードのステータスを更新して即時保存する"""
    progress = load_progress()
    progress["episodes"].setdefault(guid, {})
    progress["episodes"][guid]["status"] = status
    for k, v in kwargs.items():
        progress["episodes"][guid][k] = v
    save_progress(progress)


def check_new_episodes() -> int:
    """新エピソード数を返す（GitHub Actions用）"""
    config = load_config()
    progress = load_progress()
    done_guids = {
        guid
        for guid, info in progress["episodes"].items()
        if info.get("status") == "done"
    }
    all_episodes = parse_feed(config["rss"]["feed_url"])
    new_count = sum(1 for ep in all_episodes if ep["guid"] not in done_guids)
    return new_count


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
