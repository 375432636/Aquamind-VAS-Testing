"""Correlate a validated robot_output call with controls received by this client.

The VAS diagnostic marker is required to prove function invocation. A control
message alone may come from another VAS path and is never attributed to the tool.
"""

FIELDS = ("called", "action", "product_refs", "expression", "navigation")
BEHAVIORS = FIELDS[1:]


def validate_robot_output(spec):
    if (
        not isinstance(spec, dict)
        or not spec
        or set(spec) - set(FIELDS) - {"monitor"}
        or any(type(value) is not bool for value in spec.values())
        or spec.get("monitor") is False
    ):
        raise ValueError(
            "business.robot_output requires boolean monitor/called/action/"
            "product_refs/expression/navigation fields"
        )


def _controls(turn):
    controls = {name: [] for name in BEHAVIORS}
    controls["unattributed_image"] = []
    for event in turn.get("events", []):
        name, data = event.get("event"), event.get("data", {})
        if not isinstance(data, dict):
            continue
        if name == "action" and data.get("state", "start") == "start":
            value = data.get("action_name") or data.get("action_key")
            if value:
                controls["action"].append((value, event.get("at_ns")))
        elif name == "expression":
            value = data.get("expression")
            if isinstance(value, dict):
                value = value.get("key")
            if value:
                controls["expression"].append((value, event.get("at_ns")))
        elif name == "navigation" and isinstance(data.get("navigation"), dict):
            controls["navigation"].append((data["navigation"], event.get("at_ns")))
        elif name == "display":
            for item in data.get("items", []):
                if (
                    isinstance(item, dict)
                    and item.get("kind") == "image"
                    and item.get("url")
                ):
                    controls["product_refs"].append(("image", event.get("at_ns")))
        elif name == "image" and data.get("url"):
            controls["unattributed_image"].append(("image", event.get("at_ns")))
    return controls


def _matches(field, wanted, received):
    if field == "navigation":
        return isinstance(wanted, dict) and any(
            isinstance(value, dict)
            and value.get("action") == wanted.get("action")
            and value.get("zone_id") == wanted.get("zone_id")
            for value, _ in received
        )
    if field == "product_refs":
        return isinstance(wanted, list) and bool(wanted) and bool(received)
    return (
        isinstance(wanted, str)
        and wanted != "none"
        and any(value == wanted for value, _ in received)
    )


def evaluate_robot_output(turn, rows, spec, *, diagnostics_complete):
    validate_robot_output(spec)
    ready = [e for e in rows if e.get("event") == "robot_output_monitor_ready"]
    calls = [e for e in rows if e.get("event") == "robot_output_evaluated"]
    controls = _controls(turn)
    supported = any(type(e.get("data", {}).get("enabled")) is bool for e in ready)
    complete = diagnostics_complete is True and supported
    valid = [e for e in calls if e.get("data", {}).get("valid") is True]
    invalid = [e for e in calls if e.get("data", {}).get("valid") is False]
    fields = {}
    called = True if calls else False if complete else None
    fields["called"] = {
        "expected": spec.get("called"),
        "observed": called,
        "source": (
            "VAS robot_output_evaluated"
            if calls
            else (
                "VAS robot_output_monitor_ready"
                if complete
                else "未采集到支持监测的 VAS 诊断事件"
            )
        ),
        "diagnostic_seqs": [e.get("seq") for e in calls],
    }
    for field in BEHAVIORS:
        received = controls[field]
        requested = [e.get("data", {}).get(field) for e in valid]
        ambiguous_image = field == "product_refs" and bool(
            controls["unattributed_image"]
        )
        matched = next(
            (
                e
                for e in valid
                if _matches(field, e.get("data", {}).get(field), received)
            ),
            None,
        )
        # A complete turn can prove that a requested control did not reach the
        # test client. Missing VAS support cannot prove which path sent one.
        observed = (
            True
            if matched
            else (
                None
                if ambiguous_image and any(requested)
                else False if complete else None
            )
        )
        fields[field] = {
            "expected": spec.get(field),
            "observed": observed,
            "received": bool(received),
            "received_count": len(received),
            "unattributed_image_count": (
                len(controls["unattributed_image"]) if field == "product_refs" else 0
            ),
            "received_values": [
                value for value, _ in received if field != "product_refs"
            ],
            "requested_values": requested,
            "source": (
                "VAS robot_output_evaluated + client WebSocket"
                if matched
                else "client WebSocket" if received else "无匹配控制消息"
            ),
            "diagnostic_seq": matched.get("seq") if matched else None,
            "received_at_ns": [at for _, at in received],
        }
    results = []
    for field in FIELDS:
        expected = spec.get(field)
        observed = fields[field]["observed"]
        if expected is None:
            status = "not_applicable"
        elif observed is None:
            status = "unknown"
        else:
            status = "passed" if observed is expected else "failed"
        fields[field]["status"] = status
        results.append(status)
    # A present but rejected function call is a protocol failure even when
    # called=True is the only explicit expectation.
    validation = (
        "failed"
        if invalid
        else "passed" if valid else "unknown" if not complete else "not_called"
    )
    if invalid and spec.get("called") is not False:
        results.append("failed")
    status = (
        "failed"
        if "failed" in results
        else (
            "unknown"
            if "unknown" in results
            else "passed" if "passed" in results else "not_applicable"
        )
    )
    reason = {
        "failed": "robot_output 调用或控制消息与预期不符；查看逐项证据",
        "unknown": "缺少完整的 robot_output 诊断或归属证据",
        "passed": "robot_output 调用与所选控制消息均已观测",
        "not_applicable": "仅监测；未配置通过/失败条件",
    }[status]
    return {
        "name": "business.robot_output",
        "category": "robot_output",
        "status": status,
        "passed": status in {"passed", "not_applicable"},
        "failure_kind": (
            "diagnostic_missing"
            if status == "unknown"
            else "functional" if status == "failed" else None
        ),
        "expected": spec,
        "actual": {
            "supported": supported,
            "validation": validation,
            "call_count": len(calls),
            "invalid_reasons": [e.get("data", {}).get("reason") for e in invalid],
            "fields": fields,
        },
        "reason": reason,
    }
