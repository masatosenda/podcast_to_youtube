"""
SRTファイル生成

transcriber.pyの戻り値セグメントリストからSRTファイルを生成する。
同一話者が連続する場合はセグメントを結合する（最大5秒・最大100文字）。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _format_timestamp(seconds: float) -> str:
    """秒数をSRTタイムコード形式 HH:MM:SS,mmm に変換する"""
    ms = int(round(seconds * 1000))
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1_000
    ms %= 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _merge_segments(
    segments: list[dict],
    max_duration: float = 5.0,
    max_chars: int = 100,
) -> list[dict]:
    """同一話者の連続セグメントを結合する。"""
    if not segments:
        return []

    merged = []
    current = dict(segments[0])

    for seg in segments[1:]:
        same_speaker = seg["speaker"] == current["speaker"]
        merged_duration = seg["end"] - current["start"]
        merged_text = current["text"] + seg["text"]

        if same_speaker and merged_duration <= max_duration and len(merged_text) <= max_chars:
            current["end"] = seg["end"]
            current["text"] = merged_text
        else:
            merged.append(current)
            current = dict(seg)

    merged.append(current)
    return merged


def write_srt(
    segments: list[dict],
    output_path: str,
    speaker_colors: dict[str, str] | None = None,
    jingle_skip_seconds: float = 0,
) -> None:
    """セグメントリストからSRTファイルを生成する。"""
    merged = _merge_segments(segments)
    lines = []

    idx = 0
    for seg in merged:
        if jingle_skip_seconds > 0 and seg["start"] < jingle_skip_seconds:
            continue

        idx += 1
        start_tc = _format_timestamp(seg["start"])
        end_tc = _format_timestamp(seg["end"])
        speaker = seg["speaker"]
        text = seg["text"].strip()

        lines.append(str(idx))
        lines.append(f"{start_tc} --> {end_tc}")
        if speaker and speaker != "UNKNOWN":
            color_tag = ""
            if speaker_colors and speaker in speaker_colors:
                color_tag = r"{\c&H" + speaker_colors[speaker] + r"&}"
            lines.append(f"{color_tag}{speaker}：{text}")
        else:
            lines.append(text)
        lines.append("")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    logger.info("SRT保存: %s (%dブロック)", output_path, idx)
