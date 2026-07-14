"""State helpers for the detailed progress display on the one-click query page."""


R_DF_STEP = "化学类型图、DBE图、VK图与 DF"
IDENTIFIER_STEP = "标识符补全"
EPI_STEP = "EPI Suite 环境归趋"
COMPTOX_STEP = "EPA CompTox 用途"
ECHA_USE_STEP = "ECHA REACH 用途"
ECHA_GHS_STEP = "ECHA GHS/C&L 危害"
SOURCE_ORIGIN_STEP = "来源属性评估"
POV_STEP = "Pov-LRTP / PBM / ToxPi"


def build_selected_steps(
    *,
    run_r_replicate_df,
    run_identifier,
    run_epi,
    run_comptox,
    run_echa_use,
    run_echa_ghs,
    run_source_origin,
    run_pov_lrtp_toxpi,
):
    """Return the actual workflow stages, including automatically required dependencies."""
    steps = []
    if run_r_replicate_df or run_pov_lrtp_toxpi:
        steps.append(R_DF_STEP)
    if any(
        [
            run_identifier,
            run_epi,
            run_comptox,
            run_echa_use,
            run_echa_ghs,
            run_source_origin,
            run_pov_lrtp_toxpi,
        ]
    ):
        steps.append(IDENTIFIER_STEP)
    if run_epi or run_pov_lrtp_toxpi:
        steps.append(EPI_STEP)
    if run_comptox:
        steps.append(COMPTOX_STEP)
    if run_echa_use:
        steps.append(ECHA_USE_STEP)
    if run_echa_ghs:
        steps.append(ECHA_GHS_STEP)
    if run_source_origin:
        steps.append(SOURCE_ORIGIN_STEP)
    if run_pov_lrtp_toxpi:
        steps.append(POV_STEP)
    return steps


def create_progress_state(steps):
    return {
        "steps": list(dict.fromkeys(steps)),
        "finished_steps": set(),
        "current_step": "",
        "module_done": 0,
        "module_total": 0,
        "module_submitted": 0,
        "timeout_seconds": None,
        "active_items": {},
        "last_terminal_event": None,
    }


def record_activity_event(state, event):
    event_type = event.get("event")
    step = event.get("step", "")
    if step:
        if state["current_step"] and state["current_step"] != step:
            state["module_done"] = 0
            state["module_total"] = 0
            state["module_submitted"] = 0
            state["timeout_seconds"] = None
            state["active_items"] = {}
            state["last_terminal_event"] = None
        state["current_step"] = step

    if event_type == "stage_finished":
        if step:
            state["finished_steps"].add(step)
        if state["module_total"] and state["current_step"] == step:
            state["module_done"] = state["module_total"]
        return

    if event_type not in {"started", "completed", "failed"}:
        return

    total = int(event.get("total") or 0)
    done = int(event.get("done") or 0)
    index = int(event.get("index") or 0)
    if total:
        state["module_total"] = total
    state["module_done"] = max(state["module_done"], done)
    state["module_submitted"] = max(state["module_submitted"], index + 1)
    if event.get("timeout_seconds") is not None:
        state["timeout_seconds"] = int(event["timeout_seconds"])

    item_key = (step, index)
    if event_type == "started":
        state["active_items"][item_key] = event.get("label", "")
        return

    state["active_items"].pop(item_key, None)
    state["last_terminal_event"] = event


def progress_snapshot(state):
    steps = state["steps"]
    finished_steps = [step for step in steps if step in state["finished_steps"]]
    total = len(steps)
    current_step = state["current_step"]
    module_total = state["module_total"]
    module_done = state["module_done"]
    if current_step in state["finished_steps"]:
        module_done = module_total
    module_fraction = (module_done / module_total) if module_total else (1.0 if current_step in state["finished_steps"] else 0.0)
    return {
        "overall_finished": len(finished_steps),
        "overall_total": total,
        "overall_fraction": (len(finished_steps) / total) if total else 1.0,
        "current_step": current_step,
        "module_done": module_done,
        "module_total": module_total,
        "module_submitted": state["module_submitted"],
        "module_fraction": min(1.0, module_fraction),
        "timeout_seconds": state["timeout_seconds"],
        "active_labels": [label for label in state["active_items"].values() if label],
        "last_terminal_event": state["last_terminal_event"],
    }


def format_activity_message(snapshot):
    step = snapshot["current_step"] or "当前环节"
    total = snapshot["module_total"]
    done = snapshot["module_done"]
    submitted = snapshot["module_submitted"]
    active_labels = snapshot["active_labels"]
    if active_labels:
        labels = "、".join(active_labels[:3])
        if len(active_labels) > 3:
            labels += " 等"
        timeout = snapshot["timeout_seconds"]
        timeout_note = f"本条超时上限 {timeout} 秒。" if timeout else ""
        return (
            f"正在等待 {step} 响应：{labels}。已提交 {submitted}/{total}，"
            f"已完成 {done}/{total}，正在运行 {len(active_labels)} 条。{timeout_note}"
        )

    terminal = snapshot["last_terminal_event"]
    if terminal:
        action = "最近完成" if terminal.get("event") == "completed" else "最近失败"
        label = terminal.get("label", "当前记录")
        elapsed = terminal.get("elapsed_seconds")
        elapsed_note = f"（耗时 {elapsed:.1f} 秒）" if isinstance(elapsed, (int, float)) else ""
        return f"{step}：{action} {label}{elapsed_note}。已完成 {done}/{total}。"

    return f"正在执行 {step}。"
