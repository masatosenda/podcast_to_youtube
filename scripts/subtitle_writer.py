"""
SRTファイル生成

transcriber.pyの戻り値セグメントリストからSRTファイルを生成する。
同一話者が連続する場合はセグメントを結合する（最大5秒・最大100文字）。
"""

from pathlib import Path


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
    """
    同一話者の連続セグメントを結合する。

    結合条件:
    - 同じ話者が連続している
    - 結合後の時間が max_duration 秒以内
    - 結合後のテキストが max_chars 文字以内
    """
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


def write_srt(segments: list[dict], output_path: str) -> None:
    """
    セグメントリストからSRTファイルを生成する。

    Args:
        segments: [{"start": float, "end": float, "speaker": str, "text": str}, ...]
        output_path: 出力先SRTファイルパス
    """
    merged = _merge_segments(segments)
    lines = []

    for i, seg in enumerate(merged, start=1):
        start_tc = _format_timestamp(seg["start"])
        end_tc = _format_timestamp(seg["end"])
        speaker = seg["speaker"]
        text = seg["text"].strip()

        lines.append(str(i))
        lines.append(f"{start_tc} --> {end_tc}")
        lines.append(f"[{speaker}]: {text}")
        lines.append("")  # 空行（SRTブロック区切り）

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"[subtitle_writer] SRT保存: {output_path} ({len(merged)}ブロック)")
