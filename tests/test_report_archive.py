"""Portable session downloads preserve decoded samples and diagnostic evidence."""

import json
import shutil
import wave
from array import array

import pytest

from voice_scenarios.report_archive import export_session, restore_audio


def test_batch_archive_isolates_compression_failure_and_preserves_raw(
    tmp_path, monkeypatch
):
    from voice_scenarios import report_archive
    from voice_scenarios.batch_run import write_batch

    source = tmp_path / "run"
    (source / "sessions").mkdir(parents=True)
    items = []
    for name in ("first", "second"):
        session(source / "sessions" / name, name)
        (source / "sessions" / name / "report.html").write_text("original page")
        items.append(
            dict(
                id=name,
                name=name,
                source=name,
                directory=f"sessions/{name}",
                report=f"sessions/{name}/report.html",
                status="passed",
                turn_count=1,
            )
        )
    write_batch(source, dict(status="passed", sessions=items))
    original = (source / "batch.json").read_bytes()
    convert = report_archive._convert

    def fail_first(src, target, **kwargs):
        if "first" in src.parts:
            raise RuntimeError("ffmpeg failure")
        return convert(src, target, **kwargs)

    monkeypatch.setattr(report_archive, "_convert", fail_first)
    output = tmp_path / "download"
    batch = report_archive.export_batch(source, output)
    assert batch["status"] == "failed"
    assert batch["sessions"][0]["failure_stage"] == "archive"
    assert batch["sessions"][1]["archive_status"] == "passed"
    assert (output / "raw-fallback/first/session.played.wav").is_file()
    assert (output / "raw-fallback/first/vas-events.jsonl").read_bytes() == (
        source / "sessions/first/vas-events.jsonl"
    ).read_bytes()
    assert list((output / "sessions/second/audio").glob("*.flac"))
    assert "raw-fallback/first/report.html" in (output / "index.html").read_text()
    assert (source / "batch.json").read_bytes() == original


def write_wav(path, channels=1):
    samples = array("h", [0] * (16000 * channels) + [1200, -1200] * (8000 * channels))
    with wave.open(str(path), "wb") as out:
        out.setparams((channels, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(samples.tobytes())


def pcm(path):
    with wave.open(str(path)) as source:
        return (
            source.getnchannels(),
            source.getframerate(),
            source.readframes(source.getnframes()),
        )


def session(path, name="first"):
    path.mkdir()
    write_wav(path / "turn-001.input.wav")
    shutil.copyfile(path / "turn-001.input.wav", path / "turn-001.played.wav")
    write_wav(path / "session.played.wav", channels=2)
    write_wav(path / "session.mixed.wav")
    report = {
        "name": name,
        "status": "passed",
        "turns": [],
        "session_playback": {
            "status": "ready",
            "path": "session.played.wav",
            "playback_path": "session.mixed.wav",
            "channels": 2,
            "playback_channels": 1,
            "duration_seconds": 2,
            "zero_at_ns": 1234,
            "turns": [],
            "segments": [],
            "waits": [],
            "markers": [],
        },
    }
    (path / "report.json").write_text(json.dumps(report))
    (path / "result.json").write_text(
        json.dumps(
            {"name": name, "status": "failed", "turns": [], "error": "fixture failure"}
        )
    )
    (path / "vas-events.jsonl").write_text('{"event":"original","monotonic_ns":1234}\n')
    return report


def test_download_is_lossless_deduplicated_portable_and_does_not_modify_original(
    tmp_path,
):
    source, output = tmp_path / "source", tmp_path / "download"
    original = session(source)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    stats = export_session(source, output)
    assert stats["audio_files"] == 4
    assert stats["unique_audio_files"] == 2
    assert stats["compressed_audio_bytes"] < stats["original_audio_bytes"] / 2
    assert not list(output.rglob("*.wav"))
    assert len(list(output.rglob("*.flac"))) == 2
    assert (output / "vas-events.jsonl").read_bytes() == before["vas-events.jsonl"]
    assert (output / "result.json").read_bytes() == before["result.json"]
    report = json.loads((output / "report.json").read_text())
    for key in ("duration_seconds", "zero_at_ns", "waits", "markers"):
        assert report["session_playback"][key] == original["session_playback"][key]
    page = (output / "report.html").read_text()
    assert f'src="{report["session_playback"]["playback_path"]}"' in page
    assert "下载会话 FLAC" in page
    assert '.wav"' not in page
    restore_audio(output)
    for name in (
        "session.played.wav",
        "session.mixed.wav",
        "turn-001.input.wav",
        "turn-001.played.wav",
    ):
        assert pcm(source / name) == pcm(output / name)
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}


def test_export_does_not_include_other_sessions_and_preserves_failed_report(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    session(one, "session-not-in-download")
    report = session(two, "second")
    report["status"] = "failed"
    report["failures"] = ["interrupt_not_reached"]
    (two / "report.json").write_text(json.dumps(report))
    export_session(two, tmp_path / "download")
    result = json.loads((tmp_path / "download/report.json").read_text())
    assert result["name"] == "second"
    assert result["status"] == "failed"
    assert result["failures"] == ["interrupt_not_reached"]
    assert (
        "session-not-in-download" not in (tmp_path / "download/report.html").read_text()
    )


def test_report_command_restores_audio_for_reanalysis(tmp_path):
    from voice_scenarios.__main__ import create_report

    source = tmp_path / "original"
    session(source)
    export_session(source, tmp_path / "download")
    report = create_report(tmp_path / "download")
    assert report["status"] == "failed"
    assert pcm(source / "turn-001.input.wav") == pcm(
        tmp_path / "download/turn-001.input.wav"
    )


def test_audio_references_in_turns_and_reply_segments_are_portable(tmp_path):
    from voice_scenarios.report_archive import rewrite_audio_paths

    source = tmp_path / "source"
    session(source)
    target = tmp_path / "download"
    export_session(source, target)
    manifest = json.loads((target / "audio-manifest.json").read_text())
    data = {
        "turns": [
            {
                "audio": {"input": "/app/old/turn-001.input.wav"},
                "reply_timing": {
                    "sentences": [
                        {"audio": {"played": {"path": "turn-001.played.wav"}}}
                    ]
                },
            }
        ]
    }
    rewrite_audio_paths(data, manifest["files"])
    turn = data["turns"][0]
    assert turn["audio"]["input"].endswith(".flac")
    assert (
        turn["reply_timing"]["sentences"][0]["audio"]["played"]["path"]
        == turn["audio"]["input"]
    )


@pytest.mark.parametrize("bad_path", ["../escaped.wav", "/tmp/escaped.wav"])
def test_restore_rejects_paths_outside_download(tmp_path, bad_path):
    (tmp_path / "audio-manifest.json").write_text(
        json.dumps({"schema_version": 1, "files": {bad_path: "audio/a.flac"}})
    )
    with pytest.raises(ValueError, match="inside"):
        restore_audio(tmp_path)


def test_existing_output_and_symlinks_are_not_overwritten_or_followed(tmp_path):
    source = tmp_path / "source"
    session(source)
    output = tmp_path / "download"
    output.mkdir()
    (output / "keep.txt").write_text("keep")
    with pytest.raises(ValueError):
        export_session(source, output)
    assert (output / "keep.txt").read_text() == "keep"
    outside = tmp_path / "outside.wav"
    write_wav(outside)
    (source / "escape.wav").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        export_session(source, tmp_path / "other")
    assert not (tmp_path / "other").exists()


def test_empty_audio_and_setup_failure_still_produce_download(tmp_path):
    source = tmp_path / "setup-failure"
    source.mkdir()
    (source / "batch.json").write_text('{"status":"failed","error":"bad scenario"}')
    with wave.open(str(source / "empty.wav"), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
    export_session(source, tmp_path / "download")
    assert (tmp_path / "download/batch.json").is_file()
    assert (tmp_path / "download/empty.wav").is_file()


@pytest.mark.parametrize("count", [0, 1, 2])
def test_actions_cli_exports_only_one_session_or_setup_failure(tmp_path, count):
    import subprocess
    import sys

    root = tmp_path / "batch"
    root.mkdir()
    items = []
    for index in range(count):
        directory = root / f"session-{index}"
        session(directory, f"conversation-{index}")
        items.append({"directory": directory.name})
    (root / "batch.json").write_text(
        json.dumps({"sessions": items, "status": "passed" if count else "failed"})
    )
    output = tmp_path / "download"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "voice_scenarios.report_archive",
            str(root),
            "--single-batch",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == (2 if count > 1 else 0), process.stderr
    if count == 1:
        assert (
            json.loads((output / "report.json").read_text())["name"] == "conversation-0"
        )
        assert not (output / "session-0").exists()
    elif count == 0:
        assert json.loads((output / "batch.json").read_text())["status"] == "failed"
    else:
        assert not output.exists()
