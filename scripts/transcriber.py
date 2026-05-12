"""
Whisper文字起こし＋pyannote話者分離

音声ファイルからWhisperで文字起こし、pyannoteで話者分離し、
結合セグメントリストを返す。

speakers_by_pitch が設定されている場合、ZCR（ゼロ交差率）で
声の高さを推定し話者名を自動マッピングする。
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np
import torch
import torchaudio
# torchaudio 2.2+ で削除された set_audio_backend を補完（pyannote互換用）
if not hasattr(torchaudio, "set_audio_backend"):
    torchaudio.set_audio_backend = lambda *args, **kwargs: None

import whisper
from pyannote.audio import Pipeline

from . import load_config

logger = logging.getLogger(__name__)


# Whisperの日本語書き起こしで許容する英字表現（大文字小文字区別なし）
_ALLOWED_LATIN = {
    w.lower() for w in [
        # 一般的な略語・外来語
        "OK", "NG", "TV", "CM", "PC", "IT", "AI", "SNS", "BGM", "DJ", "MC",
        "OL", "JK", "LINE", "Zoom", "Google", "X", "Wi", "Fi", "WiFi",
        "iPhone", "iPad", "Mac", "Amazon", "Netflix", "TikTok", "Instagram",
        # ポッドキャスト関連
        "FM", "Podcast", "YouTube", "Apple", "Spotify", "note",
        # いきぬき給湯室で使いそうな英語
        "Yes", "No", "Hey", "Bye",
    ]
}


def _clean_whisper_artifacts(text: str) -> str:
    """Whisperの誤認識で混入した不自然な英字列を除去する。

    日本語音声の書き起こしで、聞き取れない部分にランダムな英単語
    （nickname, la, the 等）が混入するWhisperの典型的エラーを処理する。
    除去後にテキストが実質空になった場合は "……" を返す。
    """
    def _replace(m):
        word = m.group(0)
        if word.lower() in _ALLOWED_LATIN:
            return word
        return ""

    cleaned = re.sub(r"[A-Za-z]+", _replace, text)
    # 除去で残った不要な空白を整理
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    # 日本語テキスト間の不要な空白も除去（日本語は空白不要）
    cleaned = re.sub(r"(?<=[^\x00-\x7F]) (?=[^\x00-\x7F])", "", cleaned)
    # 意味のある文字が残っているか判定（句読点・記号のみは無意味）
    meaningful = re.sub(r"[\s？！。、…？！\.\,\?\!]", "", cleaned)
    if len(meaningful) < 2:
        return "……"
    return cleaned


def _overlap_duration(seg1_start: float, seg1_end: float,
                       seg2_start: float, seg2_end: float) -> float:
    """2セグメントの重複時間を返す"""
    start = max(seg1_start, seg2_start)
    end = min(seg1_end, seg2_end)
    return max(0.0, end - start)


def _assign_speakers_by_pitch(
    audio_path: str,
    diarization_segments: list[dict],
    speakers_by_pitch: list[str],
) -> dict[str, str]:
    """話者のピッチ（声の高さ）を推定し、低い順に名前を割り当てる。

    Zero-Crossing Rate (ZCR) をピッチの代理指標として使用。
    ZCRが低い＝声が低い＝リスト先頭の話者名を割り当て。

    Args:
        audio_path: 音声ファイルパス（WAV推奨）
        diarization_segments: pyannoteの話者分離結果
        speakers_by_pitch: 声が低い順の話者名リスト（例: ["せんだ", "かねとも"]）

    Returns:
        {pyannote_speaker_id: 表示名} のマッピング辞書
    """
    unique_speakers = sorted(set(seg["speaker"] for seg in diarization_segments))

    if len(unique_speakers) != len(speakers_by_pitch):
        logger.warning(
            "話者数が一致しません（pyannote: %d, 設定: %d）。順番にマッピングします。",
            len(unique_speakers), len(speakers_by_pitch),
        )
        return {sid: speakers_by_pitch[i] if i < len(speakers_by_pitch) else sid
                for i, sid in enumerate(unique_speakers)}

    # 音声を読み込み
    waveform, sr = torchaudio.load(audio_path)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    audio = waveform.squeeze().numpy()

    # 各話者のZCRを計算
    speaker_zcr: dict[str, float] = {}
    for speaker_id in unique_speakers:
        samples = []
        for seg in diarization_segments:
            if seg["speaker"] != speaker_id:
                continue
            start_sample = int(seg["start"] * sr)
            end_sample = int(seg["end"] * sr)
            start_sample = max(0, min(start_sample, len(audio) - 1))
            end_sample = max(0, min(end_sample, len(audio)))
            if end_sample > start_sample:
                samples.append(audio[start_sample:end_sample])

        if not samples:
            speaker_zcr[speaker_id] = 0.0
            continue

        concatenated = np.concatenate(samples)
        zcr = float(np.mean(np.abs(np.diff(np.sign(concatenated))) > 0))
        speaker_zcr[speaker_id] = zcr
        logger.info("話者 %s のZCR: %.6f", speaker_id, zcr)

    # ZCRが低い順（＝声が低い順）にソート
    sorted_speakers = sorted(speaker_zcr, key=lambda s: speaker_zcr[s])

    mapping = {sid: speakers_by_pitch[i] for i, sid in enumerate(sorted_speakers)}
    logger.info("ピッチベース話者マッピング: %s", mapping)
    return mapping


# バッチ処理時にモデルを使い回すためのキャッシュ
_whisper_cache = {"model": None, "name": None}
_pyannote_cache = {"pipeline": None, "token": None}


def _normalize_audio(audio_path: str) -> str | None:
    """pyannoteのサンプル数エラー対策: 音声をWAV 16kHz monoに変換する。

    Anchor/Spotify配信のMP3はメタデータ上の長さと実データが不一致の場合があり、
    pyannoteが "requested chunk ... resulted in N samples instead of expected M" で
    クラッシュする。事前にffmpegでWAVに変換することで解消する。
    変換後のパスを返す（呼び出し元でtmpdirを管理）。
    """
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    tmpdir = tempfile.mkdtemp(prefix="pyannote_wav_")
    wav_path = os.path.join(tmpdir, "audio_normalized.wav")
    cmd = [
        ffmpeg, "-y",
        "-i", audio_path,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        wav_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("WAV変換失敗（元ファイルで続行）: %s", result.stderr[-500:])
        return None
    logger.info("pyannote用WAV変換完了: %s", wav_path)
    return wav_path


def _get_whisper_model(model_name: str, device: str):
    """Whisperモデルを取得（同じモデル名なら再利用）"""
    if _whisper_cache["model"] is None or _whisper_cache["name"] != model_name:
        logger.info("Whisperモデル '%s' をロード中...", model_name)
        _whisper_cache["model"] = whisper.load_model(model_name, device=device)
        _whisper_cache["name"] = model_name
    return _whisper_cache["model"]


def _get_pyannote_pipeline(hf_token: str, device: str):
    """pyannoteパイプラインを取得（同じトークンなら再利用）"""
    if _pyannote_cache["pipeline"] is None or _pyannote_cache["token"] != hf_token:
        logger.info("pyannoteパイプラインをロード中...")
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            token=hf_token,
        )
        _pyannote_cache["pipeline"] = pipeline.to(torch.device(device))
        _pyannote_cache["token"] = hf_token
    return _pyannote_cache["pipeline"]


def transcribe_and_diarize(
    audio_path: str,
    whisper_model: str = "large-v3",
    language: str = "ja",
    hf_token: str | None = None,
    speaker_map: dict | None = None,
    speakers_by_pitch: list[str] | None = None,
    text_replacements: dict | None = None,
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
    logger.info("device=%s, model=%s", device, whisper_model)

    # --- Whisper 文字起こし ---
    logger.info("Whisper 文字起こし開始...")
    model = _get_whisper_model(whisper_model, device)
    result = model.transcribe(audio_path, language=language)
    whisper_segments = result["segments"]
    logger.info("Whisperセグメント数: %d", len(whisper_segments))

    # --- pyannote 話者分離 ---
    # MP3のメタデータ不整合でサンプル数エラーが出る対策: WAVに変換
    wav_path = _normalize_audio(audio_path)
    diarize_input = wav_path or audio_path
    logger.info("pyannote 話者分離開始...")
    pipeline = _get_pyannote_pipeline(hf_token, device)
    diarize_output = pipeline(diarize_input)

    # pyannote 4.x では DiarizeOutput が返る。speaker_diarization 属性から Annotation を取得
    if hasattr(diarize_output, "speaker_diarization"):
        diarization = diarize_output.speaker_diarization
    else:
        diarization = diarize_output

    # pyannoteの結果をリスト化
    diarization_segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        diarization_segments.append({
            "start": turn.start,
            "end": turn.end,
            "speaker": speaker,
        })
    logger.info("pyannoteセグメント数: %d", len(diarization_segments))

    # --- ピッチベース話者自動判定 ---
    if speakers_by_pitch and diarization_segments:
        speaker_map = _assign_speakers_by_pitch(
            diarize_input, diarization_segments, speakers_by_pitch,
        )

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

        # テキスト内の漢字表記を置換（例: 金朋→かねとも）
        if text_replacements:
            for old, new in text_replacements.items():
                text = text.replace(old, new)

        # Whisperの誤認識（ランダム英字混入）を除去
        text = _clean_whisper_artifacts(text)

        combined.append({
            "start": ws_start,
            "end": ws_end,
            "speaker": best_speaker,
            "text": text,
        })

    # WAV一時ファイルの後片付け
    if wav_path:
        try:
            os.unlink(wav_path)
            os.rmdir(os.path.dirname(wav_path))
        except OSError:
            pass

    logger.info("結合セグメント数: %d", len(combined))
    return combined


def transcribe_from_config(audio_path: str) -> list[dict]:
    """config.yaml の設定を使って文字起こし＋話者分離を実行する"""
    config = load_config()
    processing = config.get("processing", {})
    podcast = config.get("podcast", {})
    speakers_by_pitch = podcast.get("speakers_by_pitch")
    speaker_map = podcast.get("speakers", {}) if not speakers_by_pitch else None
    text_replacements = podcast.get("text_replacements", {})

    return transcribe_and_diarize(
        audio_path=audio_path,
        whisper_model=processing.get("whisper_model", "large-v3"),
        language=processing.get("language", "ja"),
        speaker_map=speaker_map,
        speakers_by_pitch=speakers_by_pitch,
        text_replacements=text_replacements,
    )
