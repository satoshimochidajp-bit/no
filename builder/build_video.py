#!/usr/bin/env python3
"""05/50のノートからAzure正規音声とバグったシーン10種類を作る。

標準ライブラリとFFmpegだけを使う。Azureの秘密情報は環境変数からのみ取得し、
ファイル・コマンドライン・検査レポートへは書き出さない。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "project.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
ASSETS = ROOT / "assets"
FRAMES = ASSETS / "frames"
AUDIO_ASSETS = ASSETS / "audio"
GIF_PATH = ROOT / CONFIG["gif"]["path"]
INPUT_PPTX = ROOT / CONFIG["source_pptx"]
CACHE_AUDIO = ROOT / "cache" / "audio"
WORK = ROOT / "WORK"
AUDIO_WORK = WORK / "audio"
CLIP_WORK = WORK / "clips"
REPORT_WORK = WORK / "reports"
OUTPUT = ROOT / "OUTPUT"
FINAL_MP4 = OUTPUT / CONFIG["output_file"]

VARIANTS = [
    ("01", "飛び出し＋画面振動"),
    ("02", "スタンプ押印"),
    ("03", "真上から落下"),
    ("04", "横滑り＋急ブレーキ"),
    ("05", "回転しながら着地"),
    ("06", "グリッチ修復"),
    ("07", "1文字ずつ衝突"),
    ("08", "左右から合体"),
    ("09", "ゴムのように伸縮"),
    ("10", "急接近＋衝撃フラッシュ"),
]


class BuildError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def stage(number: int, title: str) -> None:
    log(f"\n=== {number}/7 {title} ===")


def fnum(value: float) -> str:
    return f"{value:.3f}"


def run(command: list[str], failure: str, *, capture: bool = False) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
    except OSError as exc:
        raise BuildError(f"{failure}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stdout or "").strip() if capture else ""
        if detail:
            (REPORT_WORK / "last_ffmpeg_error.txt").write_text(detail, encoding="utf-8")
        raise BuildError(f"{failure}{': ' + detail[-1200:] if detail else ''}")
    return completed.stdout or ""


def ffprobe_json(path: Path, entries: str) -> dict:
    raw = run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        f"メディアを検査できません: {path.name}",
        capture=True,
    )
    return json.loads(raw)


def media_duration(path: Path) -> float:
    data = ffprobe_json(path, "format=duration")
    try:
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BuildError(f"長さを取得できません: {path.name}") from exc


def peak_db(path: Path) -> float:
    output = run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        f"音量を測定できません: {path.name}",
        capture=True,
    )
    match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", output)
    if not match:
        raise BuildError(f"音量の測定値を読み取れません: {path.name}")
    return float(match.group(1))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_work_dirs() -> None:
    for directory in (WORK, OUTPUT):
        if directory.exists():
            shutil.rmtree(directory)
    for directory in (AUDIO_WORK, CLIP_WORK, REPORT_WORK, OUTPUT, CACHE_AUDIO):
        directory.mkdir(parents=True, exist_ok=True)


def notes_texts(zipped: ZipFile, slide_number: int) -> list[str]:
    name = f"ppt/notesSlides/notesSlide{slide_number}.xml"
    try:
        root = ET.fromstring(zipped.read(name))
    except KeyError as exc:
        raise BuildError(f"{slide_number:02d}/50のノートが見つかりません。") from exc
    return [(node.text or "") for node in root.iter() if node.tag.endswith("}t")]


def extract_note_data() -> tuple[str, dict[str, str], dict[str, str]]:
    with ZipFile(INPUT_PPTX) as zipped:
        texts = notes_texts(zipped, CONFIG["source_slide"])
    try:
        start = texts.index("[読み上げ本文]")
        end = texts.index("[/読み上げ本文]")
    except ValueError as exc:
        raise BuildError("05/50の[読み上げ本文]ブロックを取得できません。") from exc
    if end <= start + 1:
        raise BuildError("05/50の読み上げ本文が空です。")
    narration = "".join(texts[start + 1 : end]).replace("\r", "").replace("\n", "").strip()
    metadata: dict[str, str] = {}
    for value in texts[end + 1 :]:
        if ":" in value:
            key, content = value.split(":", 1)
            metadata[key.strip()] = content.strip()
    pronunciations: dict[str, str] = {}
    for item in metadata.get("PRONUNCIATION", "").split(";"):
        if "=" in item:
            written, spoken = item.split("=", 1)
            pronunciations[written.strip()] = spoken.strip()
    return narration, metadata, pronunciations


def split_narration(narration: str) -> tuple[list[str], str]:
    segments = [match.group(0).strip() for match in re.finditer(r"[^。！？]+[。！？]", narration)]
    if len(segments) < 2:
        raise BuildError("読み上げ本文を文単位に分割できません。")
    phrase = segments[-1]
    if phrase != CONFIG["bug_phrase"]:
        raise BuildError(f"最後のセリフが「{CONFIG['bug_phrase']}」ではありません: {phrase}")
    return segments[:-1], phrase


def required_files() -> list[Path]:
    files = [
        INPUT_PPTX,
        GIF_PATH,
        AUDIO_ASSETS / "lesson_bgm.mp3",
        AUDIO_ASSETS / "bug_bgm.mp3",
        FRAMES / "slide-05.png",
        FRAMES / "slide-05-no-bug.png",
        FRAMES / "slide-06.png",
        FRAMES / "bug-text-main.png",
        FRAMES / "bug-gif-last.png",
        FRAMES / "bug-text-cyan.png",
        FRAMES / "bug-text-magenta.png",
        FRAMES / "bug-text-left.png",
        FRAMES / "bug-text-right.png",
    ]
    files.extend(FRAMES / f"bug-char-{number:02d}.png" for number in range(1, 6))
    files.extend(FRAMES / f"label_{number:02d}.png" for number in range(1, 11))
    return files


def preflight(require_azure_secrets: bool) -> tuple[str, list[str], str, dict[str, str]]:
    checks: list[str] = []
    for path in required_files():
        if not path.is_file() or path.stat().st_size == 0:
            raise BuildError(f"必要ファイルがありません、または空です: {path.relative_to(ROOT)}")
    checks.append("required_files=PASS")

    for program in ("ffmpeg", "ffprobe"):
        if not shutil.which(program):
            raise BuildError(f"{program}が見つかりません。")
    checks.append("ffmpeg_and_ffprobe=PASS")

    actual_hash = sha256_file(INPUT_PPTX)
    if actual_hash != CONFIG["source_pptx_sha256"]:
        raise BuildError(
            "入力PPTXが検証済み05/50版と一致しません。"
            f" expected={CONFIG['source_pptx_sha256']} actual={actual_hash}"
        )
    checks.append(f"pptx_sha256={actual_hash}")

    with ZipFile(INPUT_PPTX) as zipped:
        broken = zipped.testzip()
        if broken:
            raise BuildError(f"PPTX内のZIP破損を検出しました: {broken}")
        slides = [name for name in zipped.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
        if len(slides) != CONFIG["expected_slide_count"]:
            raise BuildError(f"PPTXのスライド数が{CONFIG['expected_slide_count']}枚ではありません。")
        forbidden_hits: list[str] = []
        for name in zipped.namelist():
            if not name.endswith(".xml"):
                continue
            text = zipped.read(name).decode("utf-8", errors="ignore")
            for term in CONFIG["forbidden_terms"]:
                if term in text:
                    forbidden_hits.append(f"{term}@{name}")
        if forbidden_hits:
            raise BuildError("禁止表現がPPTXに残っています: " + ", ".join(forbidden_hits[:20]))
    checks.extend(("pptx_zip_integrity=PASS", f"slide_count={len(slides)}", "forbidden_terms=PASS"))

    narration, metadata, pronunciations = extract_note_data()
    if metadata.get("SPEAKER") != CONFIG["required_speaker"]:
        raise BuildError(f"05/50の話者が{CONFIG['required_speaker']}ではありません。")
    if CONFIG["required_term"] not in narration:
        raise BuildError(f"読み上げ本文に「{CONFIG['required_term']}」がありません。")
    if pronunciations.get(CONFIG["required_term"]) != CONFIG["required_pronunciation"]:
        raise BuildError(
            f"「{CONFIG['required_term']}」の読みが「{CONFIG['required_pronunciation']}」に指定されていません。"
        )
    body, phrase = split_narration(narration)
    checks.extend(("speaker=バグっぺ", "required_term_and_pronunciation=PASS", f"body_segments={len(body)}"))

    filters = run(["ffmpeg", "-hide_banner", "-filters"], "FFmpegのフィルター一覧を取得できません。", capture=True)
    for name in ("rubberband", "acrusher", "apulsator", "aphaser", "vibrato"):
        if not re.search(rf"\b{re.escape(name)}\b", filters):
            raise BuildError(f"FFmpegに必要なフィルターがありません: {name}")
    checks.append("required_ffmpeg_filters=PASS")

    canary = REPORT_WORK / "ffmpeg_h264_aac_canary.mp4"
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=#247C84:s=320x180:r=30:d=0.5",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=0.5",
            "-shortest", "-c:v", "libx264", "-crf", "28", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2", str(canary),
        ],
        "H.264/AACの事前試験に失敗しました。",
    )
    canary_data = ffprobe_json(canary, "stream=codec_type,codec_name")
    codecs = {stream.get("codec_type"): stream.get("codec_name") for stream in canary_data.get("streams", [])}
    if codecs.get("video") != "h264" or codecs.get("audio") != "aac":
        raise BuildError("H.264/AACの事前試験に合格しませんでした。")
    checks.append("h264_aac_canary=PASS")

    gif_data = ffprobe_json(GIF_PATH, "format=duration:stream=pix_fmt")
    gif_stream = (gif_data.get("streams") or [{}])[0]
    pixel_format = str(gif_stream.get("pix_fmt", ""))
    if CONFIG["gif"]["must_have_alpha"] and "a" not in pixel_format.lower():
        raise BuildError(f"バグっぺGIFが透過タイプではありません: pix_fmt={pixel_format}")
    duration = float(gif_data["format"]["duration"])
    if abs(duration - float(CONFIG["gif"]["expected_duration_seconds"])) > 0.20:
        raise BuildError(f"GIFの動作時間が6秒ではありません: {duration:.3f}秒")
    checks.extend((f"gif_pixel_format={pixel_format}", f"gif_duration={duration:.3f}", "gif_transparency=PASS"))

    for path in (FRAMES / "slide-05.png", FRAMES / "slide-05-no-bug.png", FRAMES / "slide-06.png"):
        data = ffprobe_json(path, "stream=width,height")
        stream = (data.get("streams") or [{}])[0]
        if stream.get("width") != CONFIG["video"]["width"] or stream.get("height") != CONFIG["video"]["height"]:
            raise BuildError(f"静止画が2560×1440ではありません: {path.name}")
    checks.append("slide_frame_dimensions=PASS")

    if require_azure_secrets:
        if not os.environ.get("AZURE_SPEECH_KEY", "").strip():
            raise BuildError("GitHub Secret AZURE_SPEECH_KEY が未登録です。")
        if not os.environ.get("AZURE_SPEECH_REGION", "").strip():
            raise BuildError("GitHub Secret AZURE_SPEECH_REGION が未登録です。例: japaneast")
        checks.append("azure_secret_names_present=PASS")

    report = ["PREFLIGHT_STATUS=PASS", *checks, "", "[05/50 読み上げ本文]", narration]
    (REPORT_WORK / "PREFLIGHT_REPORT.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    (REPORT_WORK / "05-50_読み上げ本文.txt").write_text(narration + "\n", encoding="utf-8")
    return narration, body, phrase, pronunciations


def ssml_content(text: str, pronunciations: dict[str, str]) -> str:
    keys = [key for key in pronunciations if key and key in text]
    if not keys:
        return html.escape(text)
    pattern = re.compile("(" + "|".join(re.escape(key) for key in sorted(keys, key=len, reverse=True)) + ")")
    parts: list[str] = []
    for part in pattern.split(text):
        if part in pronunciations:
            parts.append(f'<sub alias="{html.escape(pronunciations[part], quote=True)}">{html.escape(part)}</sub>')
        else:
            parts.append(html.escape(part))
    return "".join(parts)


def azure_tts(text: str, output_path: Path, pronunciations: dict[str, str]) -> None:
    key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
    region = os.environ.get("AZURE_SPEECH_REGION", "").strip()
    if not key or not region:
        raise BuildError("Azure SpeechのGitHub Secretsが未登録です。")
    voice = CONFIG["voice"]
    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{voice["locale"]}">'
        f'<voice name="{voice["name"]}"><prosody rate="{voice["rate"]}" pitch="{voice["pitch"]}">'
        f"{ssml_content(text, pronunciations)}"
        "</prosody></voice></speak>"
    )
    request = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        method="POST",
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "X-Microsoft-OutputFormat": voice["azure_output_format"],
            "Content-Type": "application/ssml+xml; charset=utf-8",
            "User-Agent": "TOKYO_KOKUGO_BUG10_GITHUB_v2.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                audio = response.read()
            if len(audio) < 1000:
                raise BuildError("Azure Speechから空の音声が返されました。")
            output_path.write_bytes(audio)
            return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, BuildError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(2**attempt)
    raise BuildError(f"Azure Speechの音声生成に4回失敗しました: {last_error}")


def cached_voice(text: str, pronunciations: dict[str, str], mock_audio: bool, *, is_phrase: bool = False) -> Path:
    voice = CONFIG["voice"]
    cache_key = json.dumps(
        {
            "text": text,
            "voice": voice,
            "pronunciations": pronunciations,
            "mock": mock_audio,
            "mock_phrase": is_phrase,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
    cached = CACHE_AUDIO / f"{digest}.wav"
    if cached.is_file() and cached.stat().st_size > 1000:
        try:
            if media_duration(cached) > 0.05:
                log(f"音声キャッシュを使用: {text}")
                return cached
        except BuildError:
            cached.unlink(missing_ok=True)

    if mock_audio:
        duration = 0.80 if is_phrase else 0.22
        frequency = 510 if is_phrase else 390 + (int(digest[:2], 16) % 90)
        fade_out_start = duration - 0.06
        run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
                "-af", f"volume=0.16,afade=t=in:st=0:d=0.02,afade=t=out:st={fade_out_start:.2f}:d=0.06",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(cached),
            ],
            "模擬音声を作成できません。",
        )
        return cached

    raw = AUDIO_WORK / f"azure_{digest}_raw.wav"
    azure_tts(text, raw, pronunciations)
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
            "-af", f"atempo={voice['playback']}", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(cached),
        ],
        f"音声の再生速度を調整できません: {text}",
    )
    raw.unlink(missing_ok=True)
    return cached


def write_concat_list(paths: list[Path], destination: Path) -> None:
    lines = ["file '" + str(path.resolve()).replace("'", "'\\''") + "'" for path in paths]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_silence(path: Path, duration: float) -> None:
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={fnum(duration)}",
            "-c:a", "pcm_s16le", str(path),
        ],
        f"{duration}秒の無音を作成できません。",
    )


def effect_filter(variant_id: str, phrase_duration: float) -> str:
    if variant_id == "01":
        return "[0:a]asplit=2[main][double];[double]rubberband=pitch=0.82:formant=shifted,volume=0.42,adelay=85|85[double2];[main][double2]amix=inputs=2:normalize=0:duration=first[out]"
    if variant_id == "02":
        return "[0:a]aecho=0.82:0.45:90|180:0.30|0.14[out]"
    if variant_id == "03":
        return "[0:a]rubberband=pitch=0.84:formant=shifted:pitchq=quality[out]"
    if variant_id == "04":
        return "[0:a]apulsator=mode=sine:hz=0.75:amount=0.88[out]"
    if variant_id == "05":
        return "[0:a]apulsator=mode=sine:hz=1.4:amount=0.72,aphaser=in_gain=0.65:out_gain=0.82:delay=2.5:decay=0.35:speed=0.8:type=t[out]"
    if variant_id == "06":
        return "[0:a]acrusher=bits=9:mix=0.34,tremolo=f=17:d=0.36[out]"
    if variant_id == "07":
        points = [phrase_duration * index / 5.0 for index in range(6)]
        filters = ["[0:a]asplit=5[a1][a2][a3][a4][a5]"]
        for index, pitch in enumerate((0.88, 0.96, 1.04, 1.12, 1.20), start=1):
            filters.append(
                f"[a{index}]atrim={fnum(points[index-1])}:{fnum(points[index])},asetpts=PTS-STARTPTS,"
                f"rubberband=pitch={pitch}:formant=shifted[s{index}]"
            )
        filters.append("[s1][s2][s3][s4][s5]concat=n=5:v=0:a=1[out]")
        return ";".join(filters)
    if variant_id == "08":
        half = phrase_duration / 2.0
        return (
            f"[0:a]asplit=2[left0][right0];[left0]atrim=0:{fnum(half)},asetpts=PTS-STARTPTS,"
            "pan=stereo|c0=1.35*c0|c1=0.18*c1[left];"
            f"[right0]atrim={fnum(half)}:{fnum(phrase_duration)},asetpts=PTS-STARTPTS,"
            "pan=stereo|c0=0.18*c0|c1=1.35*c1[right];"
            "[left][right]concat=n=2:v=0:a=1[out]"
        )
    if variant_id == "09":
        return "[0:a]vibrato=f=7.2:d=0.42,rubberband=pitch=1.05:formant=shifted[out]"
    if variant_id == "10":
        return "[0:a]lowpass=f=1250,aecho=0.75:0.32:55:0.16,volume='0.48+0.52*min(t/1.25,1)':eval=frame[out]"
    raise BuildError(f"未知の音声演出IDです: {variant_id}")


def animation_filter(variant_id: str, text_start: float, gif_start: float, duration: float) -> str:
    ts = fnum(text_start)
    gs = fnum(gif_start)
    if variant_id == "01":
        p1, p2, p3 = (fnum(text_start + value) for value in (0.20, 0.40, 0.60))
        factor = f"if(lt(t,{ts}),0.6,if(lt(t,{p1}),0.6+2.75*(t-{ts}),if(lt(t,{p2}),1.15-0.75*(t-{p1}),1)))"
        return (
            f"[3:v]format=rgba,scale=w='iw*{factor}':h='ih*{factor}':eval=frame,setpts=PTS-STARTPTS[txt];"
            f"[stage][txt]overlay=x='(W-w)/2':y='(H-h)/2':enable='between(t,{ts},{gs})':eof_action=pass[pop];"
            f"[pop]pad=2596:1476:18:18:color=#FCFAF5,crop=2560:1440:"
            f"x='18+if(between(t,{p1},{p3}),16*sin((t-{p1})*85),0)':"
            f"y='18+if(between(t,{p1},{p3}),8*sin((t-{p1})*117),0)'[anim]"
        )
    if variant_id == "02":
        p1, p2 = (fnum(text_start + value) for value in (0.30, 0.55))
        return (
            f"[3:v]format=rgba,rotate=a='if(lt(t,{ts}),-0.16,if(lt(t,{p2}),-0.16*(1-(t-{ts})/0.45),0))':"
            "ow=rotw(iw):oh=roth(ih):c=none,setpts=PTS-STARTPTS[txt];"
            f"[stage][txt]overlay=x='(W-w)/2':y='if(lt(t,{ts}),-h,if(lt(t,{p1}),"
            f"-h+((H-h)/2+h)*(t-{ts})/0.30,if(lt(t,{p2}),(H-h)/2-36*sin((t-{p1})/0.25*PI),(H-h)/2)))':"
            f"enable='between(t,{ts},{gs})':eof_action=pass[anim]"
        )
    if variant_id == "03":
        p1, p2 = (fnum(text_start + value) for value in (0.27, 0.57))
        return (
            "[3:v]format=rgba,setpts=PTS-STARTPTS[txt];"
            f"[stage][txt]overlay=x='(W-w)/2':y='if(lt(t,{ts}),-h,if(lt(t,{p1}),"
            f"-h+((H-h)/2+h)*(t-{ts})/0.27,if(lt(t,{p2}),(H-h)/2-58*exp(-8*(t-{p1}))*sin((t-{p1})*28),(H-h)/2)))':"
            f"enable='between(t,{ts},{gs})':eof_action=pass[drop];"
            f"[drop]pad=2580:1460:10:10:color=#FCFAF5,crop=2560:1440:x=10:"
            f"y='10+if(between(t,{p1},{p2}),9*sin((t-{p1})*100),0)'[anim]"
        )
    if variant_id == "04":
        p1, p2 = (fnum(text_start + value) for value in (0.30, 0.55))
        return (
            "[3:v]format=rgba,setpts=PTS-STARTPTS[txt];"
            f"[stage][txt]overlay=x='if(lt(t,{ts}),-w,if(lt(t,{p1}),"
            f"-w+((W-w)/2+w+90)*(t-{ts})/0.30,if(lt(t,{p2}),(W-w)/2+90*(1-(t-{p1})/0.25),(W-w)/2)))':"
            f"y='(H-h)/2':enable='between(t,{ts},{gs})':eof_action=pass[anim]"
        )
    if variant_id == "05":
        p1 = fnum(text_start + 0.55)
        factor = f"if(lt(t,{ts}),0.22,if(lt(t,{p1}),0.22+0.78*(t-{ts})/0.55,1))"
        return (
            f"[3:v]format=rgba,scale=w='iw*{factor}':h='ih*{factor}':eval=frame,"
            f"rotate=a='if(lt(t,{ts}),6.283,if(lt(t,{p1}),6.283*(1-(t-{ts})/0.55),0))':"
            "ow=2400:oh=620:c=none,setpts=PTS-STARTPTS[txt];"
            f"[stage][txt]overlay=x='(W-w)/2':y='(H-h)/2':enable='between(t,{ts},{gs})':eof_action=pass[anim]"
        )
    if variant_id == "06":
        p1 = fnum(text_start + 0.57)
        return (
            "[6:v]format=rgba,setpts=PTS-STARTPTS[cyan];[7:v]format=rgba,setpts=PTS-STARTPTS[magenta];"
            "[3:v]format=rgba,setpts=PTS-STARTPTS[txt];"
            f"[stage][cyan]overlay=x='(W-w)/2+18*sin((t-{ts})*95)':y='(H-h)/2+8*cos((t-{ts})*71)':"
            f"enable='between(t,{ts},{p1})':eof_action=pass[g1];"
            f"[g1][magenta]overlay=x='(W-w)/2-18*sin((t-{ts})*87)':y='(H-h)/2-8*cos((t-{ts})*79)':"
            f"enable='between(t,{ts},{p1})':eof_action=pass[g2];"
            f"[g2][txt]overlay=x='(W-w)/2+if(between(t,{ts},{p1}),6*sin((t-{ts})*121),0)':y='(H-h)/2':"
            f"enable='between(t,{ts},{gs})':eof_action=pass[anim]"
        )
    if variant_id == "07":
        xs = (190, 650, 1110, 1570, 2030)
        chain: list[str] = []
        previous = "stage"
        for index, x_position in enumerate(xs):
            input_index = 10 + index
            start = fnum(text_start + index * 0.12)
            end = fnum(text_start + index * 0.12 + 0.28)
            output_name = "anim" if index == 4 else f"c{index + 1}"
            char_name = f"ch{index + 1}"
            chain.append(f"[{input_index}:v]format=rgba,setpts=PTS-STARTPTS[{char_name}]")
            chain.append(
                f"[{previous}][{char_name}]overlay=x={x_position}:"
                f"y='if(lt(t,{start}),-h,if(lt(t,{end}),-h+((H-h)/2+h)*(t-{start})/0.28,(H-h)/2))':"
                f"enable='between(t,{start},{gs})':eof_action=pass[{output_name}]"
            )
            previous = output_name
        return ";".join(chain)
    if variant_id == "08":
        p1 = fnum(text_start + 0.40)
        return (
            "[8:v]format=rgba,setpts=PTS-STARTPTS[left];[9:v]format=rgba,setpts=PTS-STARTPTS[right];"
            f"[stage][left]overlay=x='if(lt(t,{ts}),-w,if(lt(t,{p1}),-w+(355+w)*(t-{ts})/0.40,355))':"
            f"y='(H-h)/2':enable='between(t,{ts},{gs})':eof_action=pass[s1];"
            f"[s1][right]overlay=x='if(lt(t,{ts}),W,if(lt(t,{p1}),W-(W-900)*(t-{ts})/0.40,900))':"
            f"y='(H-h)/2':enable='between(t,{ts},{gs})':eof_action=pass[anim]"
        )
    if variant_id == "09":
        p1, p2, p3 = (fnum(text_start + value) for value in (0.20, 0.40, 0.60))
        sx = f"if(lt(t,{ts}),0.48,if(lt(t,{p1}),0.48+3.6*(t-{ts}),if(lt(t,{p2}),1.2-1.5*(t-{p1}),if(lt(t,{p3}),0.9+0.5*(t-{p2}),1))))"
        sy = f"if(lt(t,{ts}),1.25,if(lt(t,{p1}),1.25-2.5*(t-{ts}),if(lt(t,{p2}),0.75+1.75*(t-{p1}),if(lt(t,{p3}),1.1-0.5*(t-{p2}),1))))"
        return (
            f"[3:v]format=rgba,scale=w='iw*{sx}':h='ih*{sy}':eval=frame,setpts=PTS-STARTPTS[txt];"
            f"[stage][txt]overlay=x='(W-w)/2':y='(H-h)/2':enable='between(t,{ts},{gs})':eof_action=pass[anim]"
        )
    if variant_id == "10":
        p1, p2 = (fnum(text_start + value) for value in (0.27, 0.47))
        flash1 = fnum(text_start + 0.25)
        flash2 = fnum(text_start + 0.29)
        flash3 = fnum(text_start + 0.47)
        factor = f"if(lt(t,{ts}),0.12,if(lt(t,{p1}),0.12+3.12*(t-{ts}),if(lt(t,{p2}),1.18-0.9*(t-{p1}),1)))"
        return (
            f"[3:v]format=rgba,scale=w='iw*{factor}':h='ih*{factor}':eval=frame,setpts=PTS-STARTPTS[txt];"
            f"[stage][txt]overlay=x='(W-w)/2':y='(H-h)/2':enable='between(t,{ts},{gs})':eof_action=pass[zoom];"
            f"color=c=white:s=2560x1440:r=30:d={fnum(duration)},format=rgba,"
            f"fade=t=in:st={flash1}:d=0.04:alpha=1,fade=t=out:st={flash2}:d=0.18:alpha=1[flash];"
            f"[zoom][flash]overlay=enable='between(t,{flash1},{flash3})':eof_action=pass[anim]"
        )
    raise BuildError(f"未知の映像演出IDです: {variant_id}")


def create_audio(
    body_segments: list[str],
    phrase_text: str,
    pronunciations: dict[str, str],
    mock_audio: bool,
    fast_qa: bool,
) -> tuple[dict[str, Path], dict[str, float], dict[str, float]]:
    stage(3, "05/50ノート本文からバグっぺ音声を生成")
    segment_paths: list[Path] = []
    for index, text in enumerate(body_segments, start=1):
        log(f"音声 {index:02d}/{len(body_segments):02d}: {text}")
        segment_paths.append(cached_voice(text, pronunciations, mock_audio))
    phrase_base = cached_voice(phrase_text, pronunciations, mock_audio, is_phrase=True)

    timing = dict(CONFIG["timing"])
    if fast_qa:
        timing.update(
            pre_roll_seconds=0.20,
            sentence_pause_seconds=0.05,
            gif_motion_seconds=0.60,
            gif_final_hold_seconds=0.30,
            post_roll_seconds=0.30,
            bug_bgm_offset_seconds=0.20,
        )

    pre_roll = AUDIO_WORK / "silence_pre.wav"
    pause = AUDIO_WORK / "silence_pause.wav"
    make_silence(pre_roll, timing["pre_roll_seconds"])
    make_silence(pause, timing["sentence_pause_seconds"])
    concat_inputs = [pre_roll]
    for segment in segment_paths:
        concat_inputs.extend((segment, pause))
    body_list = AUDIO_WORK / "body_concat.txt"
    write_concat_list(concat_inputs, body_list)
    body_timeline = AUDIO_WORK / "body_timeline.wav"
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(body_list), "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(body_timeline),
        ],
        "本文音声を連結できません。",
    )

    phrase_start = media_duration(body_timeline)
    phrase_duration = media_duration(phrase_base)
    gif_start = phrase_start + phrase_duration
    gif_motion_end = gif_start + timing["gif_motion_seconds"]
    gif_end = gif_motion_end + timing["gif_final_hold_seconds"]
    raw_scene_duration = gif_end + timing["post_roll_seconds"]
    # 1シーンの終端を30fpsのフレーム境界へそろえ、10本結合後も正確に30fpsを維持する。
    fps = float(CONFIG["video"]["fps"])
    scene_duration = math.ceil(raw_scene_duration * fps - 1e-9) / fps
    bug_bgm_start = phrase_start + timing["bug_bgm_offset_seconds"]
    timeline = {
        "phrase_start": phrase_start,
        "phrase_duration": phrase_duration,
        "gif_start": gif_start,
        "gif_motion_end": gif_motion_end,
        "gif_end": gif_end,
        "scene_duration": scene_duration,
        "bug_bgm_start": bug_bgm_start,
    }
    log(
        "Timing: "
        f"phrase={fnum(phrase_start)}s / GIF={fnum(gif_start)}s / "
        f"next={fnum(gif_end)}s / scene={fnum(scene_duration)}s"
    )

    stage(4, "10種類の音声演出を作成")
    reference_peak = peak_db(phrase_base)
    log(f"『バグった！』基準ピーク: {reference_peak:.3f} dB（gainMultiplier 1.0）")
    scene_audio: dict[str, Path] = {}
    adjusted_peaks: dict[str, float] = {}
    mix_config = CONFIG["mix"]
    for variant_id, label in VARIANTS:
        effect_raw = AUDIO_WORK / f"bug_phrase_{variant_id}_effect_raw.wav"
        effect = AUDIO_WORK / f"bug_phrase_{variant_id}.wav"
        run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(phrase_base),
                "-filter_complex", effect_filter(variant_id, phrase_duration), "-map", "[out]",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(effect_raw),
            ],
            f"音声演出{variant_id}を作成できません。",
        )
        measured = peak_db(effect_raw)
        gain = reference_peak - measured
        run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(effect_raw),
                "-af", f"volume={gain:.3f}dB,afade=t=in:st=0:d=0.025",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(effect),
            ],
            f"音声演出{variant_id}の音量を調整できません。",
        )
        adjusted_peak = peak_db(effect)
        adjusted_peaks[variant_id] = adjusted_peak
        if abs(adjusted_peak - reference_peak) > 0.35:
            raise BuildError(
                f"音声演出{variant_id}の最終ピークが基準音声と一致しません。"
                f" reference={reference_peak:.2f}dB actual={adjusted_peak:.2f}dB"
            )

        scene = AUDIO_WORK / f"scene_audio_{variant_id}.wav"
        phrase_delay = round(phrase_start * 1000)
        bug_bgm_delay = round(bug_bgm_start * 1000)
        scene_text = fnum(scene_duration)
        mix = (
            f"[0:a]apad=pad_dur={scene_text},atrim=0:{scene_text}[body];"
            f"[1:a]adelay={phrase_delay}|{phrase_delay}[phrase];"
            f"[2:a]volume={mix_config['lesson_bgm_volume']},atrim=0:{scene_text}[lesson];"
            f"[3:a]volume={mix_config['bug_bgm_volume']},adelay={bug_bgm_delay}|{bug_bgm_delay},"
            f"atrim=0:{scene_text}[bugbgm];"
            f"[body][phrase][lesson][bugbgm]amix=inputs=4:normalize=0:duration=longest,"
            f"alimiter=limit={mix_config['limiter']}:attack=4:release=50,atrim=0:{scene_text}[out]"
        )
        run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(body_timeline), "-i", str(effect),
                "-stream_loop", "-1", "-i", str(AUDIO_ASSETS / "lesson_bgm.mp3"),
                "-stream_loop", "-1", "-i", str(AUDIO_ASSETS / "bug_bgm.mp3"),
                "-filter_complex", mix, "-map", "[out]", "-ar", "48000", "-ac", "2",
                "-c:a", "pcm_s16le", str(scene),
            ],
            f"音声演出{variant_id}をミックスできません。",
        )
        scene_audio[variant_id] = scene
        log(f"音声演出{variant_id}（{label}）完了 / peak={adjusted_peak:.2f}dB")
    return scene_audio, timeline, {"reference": reference_peak, **adjusted_peaks}


def common_visual_filter(timeline: dict[str, float]) -> str:
    gif_start = fnum(timeline["gif_start"])
    gif_motion_end = fnum(timeline["gif_motion_end"])
    gif_end = fnum(timeline["gif_end"])
    gif = CONFIG["gif"]
    return (
        "[0:v]format=rgba,setpts=PTS-STARTPTS[base];"
        "[1:v]format=rgba,setpts=PTS-STARTPTS[nobug];"
        "[2:v]format=rgba,setpts=PTS-STARTPTS[next0];"
        f"[base][nobug]overlay=enable='between(t,{gif_start},{gif_end})':eof_action=pass[b1];"
        f"[4:v]format=rgba,scale={gif['width']}:{gif['height']}:flags=lanczos,"
        f"setpts=PTS-STARTPTS+{gif_start}/TB[gif];"
        f"[b1][gif]overlay=x={gif['x']}:y={gif['y']}:enable='between(t,{gif_start},{gif_motion_end})':eof_action=pass[b2];"
        f"[5:v]format=rgba,scale={gif['width']}:{gif['height']}:flags=lanczos,setpts=PTS-STARTPTS[last];"
        f"[b2][last]overlay=x={gif['x']}:y={gif['y']}:enable='between(t,{gif_motion_end},{gif_end})':eof_action=pass[b3];"
        f"[next0]fade=t=in:st={gif_end}:d=0.30:alpha=1[next];"
        f"[b3][next]overlay=enable='gte(t,{gif_end})':eof_action=pass[stage]"
    )


def render_video(scene_audio: dict[str, Path], timeline: dict[str, float], fast_qa: bool) -> Path:
    stage(5, "2K・30fpsで10種類の映像を作成" if not fast_qa else "短縮QA映像を10種類作成")
    duration_text = fnum(timeline["scene_duration"])
    common = common_visual_filter(timeline)
    clips: list[Path] = []
    frame_inputs = [
        FRAMES / "slide-05.png",
        FRAMES / "slide-05-no-bug.png",
        FRAMES / "slide-06.png",
        FRAMES / "bug-text-main.png",
    ]
    additional_inputs = [
        FRAMES / "bug-gif-last.png",
        FRAMES / "bug-text-cyan.png",
        FRAMES / "bug-text-magenta.png",
        FRAMES / "bug-text-left.png",
        FRAMES / "bug-text-right.png",
        FRAMES / "bug-char-01.png",
        FRAMES / "bug-char-02.png",
        FRAMES / "bug-char-03.png",
        FRAMES / "bug-char-04.png",
        FRAMES / "bug-char-05.png",
    ]
    for variant_id, label in VARIANTS:
        log(f"映像演出{variant_id}: {label}")
        animation = animation_filter(
            variant_id,
            timeline["phrase_start"],
            timeline["gif_start"],
            timeline["scene_duration"],
        )
        if fast_qa:
            tail = "[16:v]format=rgba,setpts=PTS-STARTPTS[label];[anim][label]overlay=1780:1342:eof_action=pass[vlabel];[vlabel]scale=640:360:flags=bilinear[vout]"
        else:
            tail = "[16:v]format=rgba,setpts=PTS-STARTPTS[label];[anim][label]overlay=1780:1342:eof_action=pass[vout]"
        filter_path = CLIP_WORK / f"filter_{variant_id}.txt"
        filter_path.write_text(f"{common};{animation};{tail}\n", encoding="utf-8")

        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for input_path in frame_inputs:
            command.extend(("-loop", "1", "-framerate", "30", "-t", duration_text, "-i", str(input_path)))
        command.extend(("-ignore_loop", "1", "-i", str(GIF_PATH)))
        for input_path in additional_inputs:
            command.extend(("-loop", "1", "-framerate", "30", "-t", duration_text, "-i", str(input_path)))
        command.extend(("-i", str(scene_audio[variant_id])))
        command.extend(
            (
                "-loop", "1", "-framerate", "30", "-t", duration_text,
                "-i", str(FRAMES / f"label_{variant_id}.png"),
            )
        )
        clip = CLIP_WORK / f"bug_scene_{variant_id}.mp4"
        command.extend(
            (
                "-filter_complex_script", str(filter_path), "-map", "[vout]", "-map", "15:a:0",
                "-t", duration_text, "-r", "30", "-c:v", "libx264",
                "-preset", "ultrafast" if fast_qa else CONFIG["video"]["preset"],
                "-crf", "28" if fast_qa else str(CONFIG["video"]["crf"]),
                "-threads", "4", "-pix_fmt", CONFIG["video"]["pixel_format"],
                "-c:a", CONFIG["audio"]["codec"], "-b:a", CONFIG["audio"]["bitrate"],
                "-ar", str(CONFIG["audio"]["sample_rate"]), "-ac", str(CONFIG["audio"]["channels"]),
                "-movflags", "+faststart", str(clip),
            )
        )
        run(command, f"映像演出{variant_id}を書き出せません。")
        clips.append(clip)

    stage(6, "10種類を1本のMP4へ結合")
    concat_list = CLIP_WORK / "concat_10.txt"
    write_concat_list(clips, concat_list)
    final_path = FINAL_MP4 if not fast_qa else OUTPUT / "QA_FAST_05-50_バグったシーン10種類.mp4"
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-c:v", "copy",
            "-bsf:v", "setts=pts=N/(30*TB):dts=N/(30*TB)",
            "-c:a", "aac", "-b:a", "256k",
            "-movflags", "+faststart", str(final_path),
        ],
        "10種類のMP4を結合できません。",
    )
    return final_path


def validate_final(
    final_path: Path,
    narration: str,
    timeline: dict[str, float],
    peaks: dict[str, float],
    fast_qa: bool,
    mock_audio: bool,
) -> None:
    stage(7, "最終検査")
    data = ffprobe_json(
        final_path,
        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
    )
    video = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"), None)
    if not video or not audio:
        raise BuildError("完成MP4に映像または音声ストリームがありません。")
    expected_width, expected_height = (640, 360) if fast_qa else (
        CONFIG["video"]["width"],
        CONFIG["video"]["height"],
    )
    if video.get("codec_name") != "h264" or video.get("width") != expected_width or video.get("height") != expected_height:
        raise BuildError("完成MP4の映像仕様が不正です。")
    if video.get("r_frame_rate") != "30/1":
        raise BuildError("完成MP4が30fpsではありません。")
    if audio.get("codec_name") != "aac" or audio.get("sample_rate") != "48000" or audio.get("channels") != 2:
        raise BuildError("完成MP4の音声がAAC・48kHz・ステレオではありません。")
    final_duration = float(data["format"]["duration"])
    expected_duration = timeline["scene_duration"] * 10
    if abs(final_duration - expected_duration) > 1.0:
        raise BuildError(
            f"完成MP4の長さが10シーン分と一致しません。expected={expected_duration:.3f} actual={final_duration:.3f}"
        )
    if final_path.stat().st_size < 100_000:
        raise BuildError("完成MP4のファイルサイズが小さすぎます。")

    report = [
        "BUILD_STATUS=PASS",
        f"BUILD_MODE={'MOCK_FAST_QA' if mock_audio and fast_qa else 'AZURE_PRODUCTION'}",
        "SOURCE_SLIDE=05/50",
        "NEXT_SLIDE=06/50",
        "NARRATION_SOURCE=PowerPoint notes [読み上げ本文]",
        f"SPEAKER={CONFIG['required_speaker']}",
        f"VOICE={CONFIG['voice']['name']}",
        f"RATE={CONFIG['voice']['rate']}",
        f"PITCH={CONFIG['voice']['pitch']}",
        f"PLAYBACK={CONFIG['voice']['playback']}",
        f"GAIN_MULTIPLIER={CONFIG['voice']['gain_multiplier']}",
        "BUG_PHRASE_VOLUME=加工前の正規バグっぺ音声と同じピークに調整",
        f"TERM_RULE={CONFIG['required_term']}",
        f"TERM_PRONUNCIATION={CONFIG['required_pronunciation']}",
        "GIF_TRANSPARENCY=PASS",
        "NORMAL_BUG_CHARACTER_HIDDEN_DURING_GIF=YES",
        f"PHRASE_START_SECONDS={fnum(timeline['phrase_start'])}",
        f"GIF_START_SECONDS={fnum(timeline['gif_start'])}",
        f"GIF_MOTION_END_SECONDS={fnum(timeline['gif_motion_end'])}",
        f"NEXT_SLIDE_SECONDS={fnum(timeline['gif_end'])}",
        f"SCENE_SECONDS={fnum(timeline['scene_duration'])}",
        f"FINAL_DURATION_SECONDS={final_duration:.3f}",
        f"FINAL_SIZE_BYTES={final_path.stat().st_size}",
        f"VIDEO=H.264 {expected_width}x{expected_height} 30fps {'QA' if fast_qa else 'CRF14'}",
        "AUDIO=AAC 256kbps 48000Hz stereo",
        f"REFERENCE_BUG_PHRASE_PEAK_DB={peaks['reference']:.2f}",
    ]
    for variant_id, label in VARIANTS:
        report.append(f"VARIANT_{variant_id}={label}; peak={peaks[variant_id]:.2f}dB; PASS")
    report.extend((f"FINAL_FILE={final_path.name}", "", "[読み上げ本文]", narration))
    report_text = "\n".join(report) + "\n"
    (OUTPUT / "BUILD_REPORT.txt").write_text(report_text, encoding="utf-8")
    shutil.copy2(REPORT_WORK / "PREFLIGHT_REPORT.txt", OUTPUT / "PREFLIGHT_REPORT.txt")
    log(f"\n完成しました: {final_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true", help="素材・ノート・FFmpegだけを検査して終了")
    parser.add_argument("--require-azure-secrets", action="store_true", help="事前検査でAzureのSecret名も確認")
    parser.add_argument("--mock-audio", action="store_true", help="Azureへ接続せず検査用の模擬音を使う")
    parser.add_argument("--fast-qa", action="store_true", help="映像時間と解像度を縮めて全10演出を検査")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    clean_work_dirs()
    try:
        stage(1, "素材・PPTX・FFmpegの事前確認")
        require_secrets = args.require_azure_secrets or not args.mock_audio
        narration, body_segments, phrase, pronunciations = preflight(require_secrets)
        log(f"05/50の話者: {CONFIG['required_speaker']}")
        log(f"05/50のノート本文: {narration}")
        if args.preflight_only:
            log("\n事前検査に合格しました。")
            return 0

        stage(2, "Azure Speech設定" if not args.mock_audio else "模擬音声設定")
        if args.mock_audio:
            log("Azureには接続しません。音声・映像フィルターの全工程を模擬音で検査します。")
        else:
            log(
                f"Region: {os.environ['AZURE_SPEECH_REGION']} / Voice: {CONFIG['voice']['name']} / "
                f"rate {CONFIG['voice']['rate']} / pitch {CONFIG['voice']['pitch']} / playback {CONFIG['voice']['playback']}"
            )
        scene_audio, timeline, peaks = create_audio(
            body_segments, phrase, pronunciations, args.mock_audio, args.fast_qa
        )
        final_path = render_video(scene_audio, timeline, args.fast_qa)
        validate_final(final_path, narration, timeline, peaks, args.fast_qa, args.mock_audio)
        return 0
    except BuildError as exc:
        REPORT_WORK.mkdir(parents=True, exist_ok=True)
        (REPORT_WORK / "BUILD_ERROR.txt").write_text(f"BUILD_STATUS=FAIL\nERROR={exc}\n", encoding="utf-8")
        print(f"\n[ERROR] {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
