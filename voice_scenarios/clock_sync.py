"""Four timestamp estimates. Raw clocks and local durations are never rewritten."""

import asyncio
import time
import uuid


class ClockSyncSamples:
    def __init__(self, samples=()):
        self.samples = list(samples)

    def summary(self):
        valid = []
        for sample in self.samples:
            try:
                if (
                    not isinstance(sample.get("server_session_id"), str)
                    or not sample["server_session_id"]
                ):
                    continue
                t1, t2, t3, t4 = (int(sample[k]) for k in ("t1", "t2", "t3", "t4"))
                rtt = t4 - t1 - (t3 - t2)
                if t4 < t1 or t3 < t2 or rtt < 0 or rtt > 1_000_000_000:
                    continue
                valid.append(
                    dict(
                        sample,
                        offset_ns=((t2 - t1) + (t3 - t4)) // 2,
                        rtt_ns=rtt,
                        uncertainty_ns=(rtt + 1) // 2,
                    )
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        result = dict(status="unavailable", samples=self.samples)
        if not valid:
            return result
        best = min(valid, key=lambda s: s["rtt_ns"])
        result.update(
            {
                k: best[k]
                for k in ("offset_ns", "rtt_ns", "uncertainty_ns", "server_session_id")
            }
        )
        result["status"] = "calibrated"
        phases = {s.get("phase", "start") for s in valid}
        selected = [
            min(
                (s for s in valid if s.get("phase", "start") == phase),
                key=lambda s: s["rtt_ns"],
            )
            for phase in phases
        ]
        if len({s["server_session_id"] for s in valid}) != 1 or any(
            abs(s["offset_ns"] - best["offset_ns"])
            > s["uncertainty_ns"] + best["uncertainty_ns"] + 5_000_000
            for s in selected
        ):
            result["status"] = "unstable"
        return result


async def collect_clock_samples(send, pending, anchor, phase="start", budget=1.0):
    """Nonblocking caller task; a legacy server costs at most one probe timeout."""
    samples = []
    try:
        async with asyncio.timeout(budget):
            for _ in range(5):
                key = uuid.uuid4().hex
                future = asyncio.get_running_loop().create_future()
                pending[key] = future
                try:
                    t1 = (
                        anchor["wall_time_ns"]
                        + time.monotonic_ns()
                        - anchor["monotonic_ns"]
                    )
                    await send({"type": "clock_sync", "request_id": key})
                    message, received = await asyncio.wait_for(future, 0.25)
                    samples.append(
                        dict(
                            t1=t1,
                            t2=message.get("server_received_ns"),
                            t3=message.get("server_sent_ns"),
                            t4=anchor["wall_time_ns"]
                            + received
                            - anchor["monotonic_ns"],
                            phase=phase,
                            server_session_id=message.get("server_session_id", ""),
                        )
                    )
                finally:
                    pending.pop(key, None)
                await asyncio.sleep(0.015)
    except (TimeoutError, ConnectionError, OSError):
        pass
    return samples
