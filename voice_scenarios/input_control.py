"""Separate manual stop evidence from local and provider VAD observations."""


def refresh_ptt_markers(playback, turns):
    """Derive manual audio boundaries from sent controls, including old reports.

    These are client control timestamps, not inferred acoustic/VAD boundaries.
    A failed or missing stop must never produce an end marker.
    """
    zero = playback.get("zero_at_ns")
    if zero is None:
        return
    points = []
    for index, turn in enumerate(turns, 1):
        if (
            turn.get("input_settings", {}).get("mode") != "manual"
            or turn.get("input_type", "audio") != "audio"
            or turn.get("sensor")
        ):
            continue
        for event in turn.get("events", []):
            kind = {
                "listen_start_sent": "ptt_start",
                "listen_stop_sent": "ptt_stop",
            }.get(event["event"])
            if kind and event.get("at_ns") is not None:
                points.append(
                    dict(
                        turn_index=index,
                        kind=kind,
                        at_seconds=(event["at_ns"] - zero) / 1e9,
                    )
                )
    ends = {
        (p["turn_index"], round(p["at_seconds"] * 1e9))
        for p in points
        if p["kind"] == "ptt_stop"
    }
    playback["markers"] = [
        marker
        for marker in playback.get("markers", [])
        if marker["kind"] not in {"ptt_start", "ptt_stop"}
        and not (
            marker["kind"] == "input_end"
            and (marker["turn_index"], round(marker["at_seconds"] * 1e9)) in ends
        )
    ] + points


def _vad_setting(events, name):
    values = [
        e.get("data", {}).get("vad_enabled") for e in events if e["event"] == name
    ]
    if any(value is True for value in values):
        return "on"
    if values and all(value is False for value in values):
        return "off"
    return "unknown"


def inspect_input_control(turn):
    client = turn.get("events", [])
    server = turn.get("vas_events", [])
    stops = [e for e in server if e["event"] == "listen_stop_received"]
    commits = [e for e in server if e["event"] == "asr_commit_sent"]
    finals = [e for e in server if e["event"] == "asr_final"]
    mode = turn.get("input_settings", {}).get("mode", "unknown")
    finalization = "unknown"
    if mode == "manual":
        for stop in stops:
            clock = stop.get("clock_id")
            at = stop.get("monotonic_ns")
            if not clock or at is None:
                continue
            same = [
                e
                for e in finals
                if e.get("clock_id") == clock and e.get("monotonic_ns") is not None
            ]
            if any(e["monotonic_ns"] < at for e in same):
                finalization = "early_final"
                break
            if any(
                c.get("clock_id") == clock
                and c.get("monotonic_ns") is not None
                and at <= c["monotonic_ns"] <= f["monotonic_ns"]
                for c in commits
                for f in same
            ):
                finalization = "manual_commit"
    local = [
        e
        for e in server
        if e["event"]
        in {
            "local_vad_speech_started",
            "local_vad_last_voice",
            "local_vad_endpoint_detected",
        }
    ]
    return dict(
        mode=mode,
        stop_sent=sum(e["event"] == "listen_stop_sent" for e in client),
        stop_received=len(stops),
        requested_vad=_vad_setting(server, "asr_session_config_requested"),
        confirmed_vad=_vad_setting(server, "asr_session_config_confirmed"),
        local_vad="observed" if local else "unknown",
        local_vad_events=len(local),
        finalization=finalization,
    )
