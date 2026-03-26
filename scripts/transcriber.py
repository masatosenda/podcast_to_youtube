"""
Whisper文字起こし＋pyannote話者分離

音声ファイルからWhisperで文字起こし、pyannoteで話者分離し、
結合セグメントリストを返す。

注意: pyannoteの話者IDはエピソードごとにリセットされます。
SPEAKER_00が毎回同じ人物とは限らないため、config.yamlの speakers
マッピングは手動確認・補正が必要な場合があります。
"""

import os
from pathlib import Path

import torch
import whisper
import yaml
from pyannote.audio import Pipeline

ROOT_DIR = Path(__file__).parent.parent
CONFIG_FILE = ROOT_DIR / "config" / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _overlap_duration(seg1_start: float, seg1_end: float,
                       seg2_start: float, seg2_end: float) -> float:
    """2セグメントの重複時間を返す"""
    start = max(seg1_start, seg2_start)
    end = min(seg1_end, seg2_end)
    return max(0.0, end - start)


def transcribe_and_diarize(
    audio_path: str,
    whisper_model: str = "large-v3",
    language: str = "ja",
    hf_token: str | None = None,
    speaker_map: dict | None = None,
) -> list[dict]:
    """
    音声ファイルを文字起こし＋話者分離してセグメントリストを返す。

    Returns:
        [{"start": float, "end": float, "speaker": str, "text": str}, ...]
    """
    if hf_token is None:
        hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise ValueError("HF_TOKEN が設定されていません。環境変数 HF_TOKEN を設定してください。")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[transcriber] device={device}, model={whisper_model}")

    # --- Whisper 文字起こし ---
    print("[transcriber] Whisper 文字起こし開始...")
    model = whisper.load_model(whisper_model, device=device)
    result = model.transcribe(audio_path, language=language)
    whisper_segments = result["segments"]
    print(f"[transcriber] Whisperセグメント数: {len(whisper_segments)}")

    # --- pyannote 話者分離 ---
    print("[transcriber] pyannote 話者分離開始...")
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
    )
    pipeline = pipeline.to(torch.device(device))
    diarization = pipeline(audio_path)

    # pyannoteの結果をリスト化
    diarization_segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        diarization_segments.append({
            "start": turn.start,
            "end": turn.end,
            "speaker": speaker,
        })
    print(f"[transcriber] pyannoteセグメント数: {len(diarization_segments)}")

    # --- Whisperセグメントに話者を割り当て ---
    combined = []
    for ws in whisper_segments:
        ws_start = ws["start"]
        ws_end = ws["end"]
        text = ws["text"].strip()
        if not text:
            continue

        # 最も重複時間が長いpyannoteセグメントの話者を採用
        best_speaker = "UNKNOWN"
        best_overlap = 0.0
        for ds in diarization_segments:
            overlap = _overlap_duration(ws_start, ws_end, ds["start"], ds["end"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = ds["speaker"]

        # speaker_mapで日本語名に変換
        if speaker_map:
            best_speaker = speaker_map.get(best_speaker, best_speaker)

        combined.append({
            "start": ws_start,
            "end": ws_end,
            "speaker": best_speaker,
            "text": text,
        })

    print(f"[transcriber] 結合セグメント数: {len(combined)}")
    return combined


def transcribe_from_config(audio_path: str) -> list[dict]:
    """config.yaml の設定を使って文字起こし＋話者分離を実行する"""
    config = load_config()
    processing = config.get("processing", {})
    speaker_map = config.get("podcast", {}).get("speakers", {})

    return transcribe_and_diarize(
        audio_path=audio_path,
        whisper_model=processing.get("whisper_model", "large-v3"),
        language=processing.get("language", "ja"),
        speaker_map=speaker_map,
    )
