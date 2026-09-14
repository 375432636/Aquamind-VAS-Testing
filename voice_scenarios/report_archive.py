"""Export one portable session with deduplicated, lossless FLAC audio."""

import argparse
import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from .report import build_report

MANIFEST = "audio-manifest.json"


def _inside(root, name):
    path = root / name
    if Path(name).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Audio paths must stay inside the session directory")
    return path


def _convert(source, target, *, restore=False):
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-i",
        str(source),
    ]
    command += (
        ["-map_metadata", "-1", "-c:a", "pcm_s16le"]
        if restore
        else ["-map_metadata", "-1", "-c:a", "flac", "-compression_level", "8"]
    )
    subprocess.run(
        command + [str(target)], check=True, capture_output=True, timeout=180
    )


def rewrite_audio_paths(report, files):
    """Rewrite presentation audio fields, leaving raw diagnostic events untouched."""

    def reference(value):
        if not isinstance(value, str):
            return value
        return files.get(value, files.get(Path(value).name, value))

    playback = report.get("session_playback", {})
    for key in ("path", "playback_path"):
        if key in playback:
            playback[key] = reference(playback[key])
    for turn in report.get("turns", []):
        for key, value in turn.get("audio", {}).items():
            turn["audio"][key] = reference(value)
        for sentence in turn.get("reply_timing", {}).get("sentences", []):
            for audio in sentence.get("audio", {}).values():
                if isinstance(audio, dict) and "path" in audio:
                    audio["path"] = reference(audio["path"])


def export_session(source, output):
    """Write a separate download; never change the source run or existing output."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir():
        raise ValueError("Session directory does not exist")
    if output.exists() or output.is_relative_to(source):
        raise ValueError("Choose a new output directory outside the source session")
    paths = sorted(source.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Session downloads cannot include symlinks")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".session-export-", dir=output.parent
    ) as tmp:
        staged = Path(tmp) / "download"
        staged.mkdir()
        files, encoded = {}, {}
        original_bytes = 0
        for path in paths:
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            target = staged / relative
            if path.suffix.lower() == ".wav":
                with wave.open(str(path)) as audio:
                    compress = audio.getnframes() > 0
                    if audio.getsampwidth() != 2:
                        raise ValueError("Expected recorded 16-bit PCM audio")
                if compress:
                    original_bytes += path.stat().st_size
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    asset = f"audio/{digest}.flac"
                    if digest not in encoded:
                        (staged / "audio").mkdir(exist_ok=True)
                        _convert(path, staged / asset)
                        encoded[digest] = (staged / asset).stat().st_size
                    files[relative] = asset
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        stats = {
            "audio_files": len(files),
            "unique_audio_files": len(encoded),
            "original_audio_bytes": original_bytes,
            "compressed_audio_bytes": sum(encoded.values()),
        }
        manifest = {"schema_version": 1, "files": files, "stats": stats}
        (staged / MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report_path = staged / "report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            rewrite_audio_paths(report, files)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            build_report(report, staged / "report.html")
        staged.rename(output)
    return stats


def restore_audio(directory):
    """Restore PCM for explicit report regeneration; ordinary playback uses FLAC."""
    root = Path(directory).resolve()
    manifest = root / MANIFEST
    if not manifest.is_file():
        return
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported audio manifest version")
    pairs = [
        (_inside(root, name), _inside(root, asset))
        for name, asset in data["files"].items()
    ]
    for target, source in pairs:
        if target.suffix != ".wav" or source.suffix != ".flac":
            raise ValueError("Expected WAV to FLAC audio mapping")
    for target, source in pairs:
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Failed conversions leave no half-written WAV for the next regeneration.
        with tempfile.TemporaryDirectory(prefix=".audio-restore-", dir=root) as tmp:
            restored = Path(tmp) / "restored.wav"
            _convert(source, restored, restore=True)
            restored.rename(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--single-batch",
        action="store_true",
        help="Export the sole session from a one-session batch run",
    )
    args = parser.parse_args()
    source = args.source
    if args.single_batch and (source / "batch.json").is_file():
        batch = json.loads((source / "batch.json").read_text(encoding="utf-8"))
        if len(batch["sessions"]) > 1:
            parser.error(
                "Expected one session; export each session directory separately"
            )
        if (
            batch["sessions"]
            and (source / batch["sessions"][0]["directory"] / "report.json").is_file()
        ):
            source = _inside(source, batch["sessions"][0]["directory"])
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stats = export_session(source, args.output)
    logging.info(
        "Session download: %s | audio %.2f → %.2f MiB",
        args.output,
        stats["original_audio_bytes"] / 1048576,
        stats["compressed_audio_bytes"] / 1048576,
    )


if __name__ == "__main__":
    main()
