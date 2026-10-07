"""持续追求的目标：自主 Agent 循环，逐步拆解、执行、评估，直到达成可交付结果。

启动后进入自治模式——LLM 负责规划子任务、调用工具执行、判断是否达标，
然后自动推进下一步，不需要用户每轮确认。
"""

import json
import os
import re
import threading
import time
import uuid

from . import llm, memory, review_agent
from .tools import (
    TOOL_SCHEMAS,
    approval_needed,
    approval_rememberable,
    auto_approved,
    clear_session_context,
    cleanup_session_temp,
    execute_tool,
    hook_failures,
    media_abs,
    permission_mode,
    record_approval_rule,
    reset_session_context,
    set_approved_context,
    set_run_control,
    set_session_context,
    session_work_root,
    tool_paths,
    describe_tool,
)
from .tracing import Trace

# 目标模式不限制轮数和时间，但加一个安全上限防止无限循环
PURSUIT_MAX_ROUNDS = 2000
PURSUIT_SAFETY_TIMEOUT = 7200  # 2 小时硬性安全超时
PURSUIT_LLM_TIMEOUT = 120     # 每次 LLM 调用超时

_PLAN_SYSTEM_PROMPT = """\
你是一个目标执行专家。用户给你一个最终目标，你需要：
1. 先分析这个目标，拆解为清晰的、可执行的子任务列表
2. 每个子任务应该是一个具体的、可以用工具完成的步骤
3. 按依赖顺序排列，但独立的步骤可以标注为可并行

输出格式（严格 JSON）：
{
  "analysis": "对目标的简要分析",
  "plan": [
    {"id": 1, "task": "具体任务描述", "depends_on": [], "status": "pending"},
    {"id": 2, "task": "具体任务描述", "depends_on": [1], "status": "pending"}
  ]
}

只输出 JSON，不要其他文字。"""

_EXEC_SYSTEM_PROMPT = """\
你是一个自主执行 Agent。你的任务是完成当前子任务。

规则：
1. 仔细分析当前子任务需要什么
2. 使用可用的工具（读写文件、运行代码、搜索、下载等）来完成它
3. 每轮可以调用多个工具
4. 完成后输出执行结果和完成状态

输出格式（严格 JSON，在所有工具调用完成之后的最后一轮输出）：
{
  "status": "completed" | "failed" | "partial" | "needs_input",
  "result": "执行结果的详细描述",
  "deliverables": ["产出的文件路径或关键结果"],
  "next_hint": "对下一步的建议（如果有）"
}

注意：
- 只在最后一轮（所有工具都调完、任务完成或失败时）才输出这个 JSON
- 中间轮次正常使用工具，不需要输出 JSON
- 如果遇到需要用户确认的信息，设 status 为 "needs_input"
- 把关键数字、文件路径、代码结果写进 result"""

_EVAL_SYSTEM_PROMPT = """\
你是一个目标评估专家。评估当前计划的整体进展。

输入：
- 最终目标
- 执行计划及每个子任务的状态和结果

输出格式（严格 JSON）：
{
  "overall_status": "completed" | "in_progress" | "blocked" | "needs_revision",
  "progress_pct": 0-100,
  "summary": "当前进展总结",
  "revision": null 或 {
    "add": [{"task": "新增任务描述", "depends_on": []}],
    "remove": [被移除的子任务 id],
    "modify": [{"id": 任务id, "task": "修改后的描述"}]
  },
  "deliverable_summary": "如果 overall_status 是 completed，总结最终交付物"
}

只输出 JSON。如果目标已经完成，设 overall_status 为 completed。"""


# ── 数据库操作 ──

def _ensure_table():
    with memory._conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pursuits (
                id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                plan_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'planning'
                    CHECK(status IN ('planning','running','paused','completed','failed','stopped')),
                progress_pct INTEGER NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pursuit_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pursuit_id TEXT NOT NULL,
                step_id INTEGER NOT NULL,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','running','completed','failed','skipped')),
                result TEXT NOT NULL DEFAULT '',
                deliverables TEXT NOT NULL DEFAULT '[]',
                started_at REAL,
                finished_at REAL,
                FOREIGN KEY (pursuit_id) REFERENCES pursuits(id)
            )
        """)


def create_pursuit(goal: str, session_id: str = "") -> dict:
    """创建新的追求目标。"""
    _ensure_table()
    pid = uuid.uuid4().hex[:12]
    now = time.time()
    with memory._conn() as conn:
        conn.execute(
            "INSERT INTO pursuits(id, goal, status, session_id, created_at, updated_at) "
            "VALUES (?,?,'planning',?,?,?)",
            (pid, goal.strip(), session_id, now, now),
        )
    return {"id": pid, "goal": goal.strip(), "status": "planning"}


def get_pursuit(pursuit_id: str) -> dict | None:
    _ensure_table()
    with memory._conn() as conn:
        row = conn.execute("SELECT * FROM pursuits WHERE id=?", (pursuit_id,)).fetchone()
        if not row:
            return None
        p = dict(row)
        p["plan"] = json.loads(p.get("plan_json") or "[]")
        steps = conn.execute(
            "SELECT * FROM pursuit_steps WHERE pursuit_id=? ORDER BY id", (pursuit_id,)
        ).fetchall()
        p["steps"] = [dict(s) for s in steps]
        return p


def get_active_pursuit() -> dict | None:
    """获取当前活跃的追求目标。"""
    _ensure_table()
    with memory._conn() as conn:
        row = conn.execute(
            "SELECT * FROM pursuits WHERE status IN ('planning','running','paused') "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return get_pursuit(row["id"])


def update_pursuit(pursuit_id: str, **kwargs) -> None:
    allowed = {"status", "plan_json", "progress_pct", "summary", "finished_at"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [pursuit_id]
    with memory._conn() as conn:
        conn.execute(f"UPDATE pursuits SET {sets} WHERE id=?", vals)


def save_step(pursuit_id: str, step_id: int, task: str, status: str = "pending",
              result: str = "", deliverables: list | None = None) -> None:
    now = time.time()
    with memory._conn() as conn:
        existing = conn.execute(
            "SELECT id FROM pursuit_steps WHERE pursuit_id=? AND step_id=?",
            (pursuit_id, step_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE pursuit_steps SET task=?, status=?, result=?, deliverables=?, "
                "finished_at=? WHERE pursuit_id=? AND step_id=?",
                (task, status, result, json.dumps(deliverables or []),
                 now if status in ("completed", "failed", "skipped") else None,
                 pursuit_id, step_id),
            )
        else:
            conn.execute(
                "INSERT INTO pursuit_steps(pursuit_id, step_id, task, status, result, "
                "deliverables, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?)",
                (pursuit_id, step_id, task, status, result,
                 json.dumps(deliverables or []),
                 now if status == "running" else None,
                 now if status in ("completed", "failed", "skipped") else None),
            )


def stop_pursuit(pursuit_id: str) -> dict | None:
    p = get_pursuit(pursuit_id)
    if not p:
        return None
    update_pursuit(pursuit_id, status="stopped", finished_at=time.time())
    return get_pursuit(pursuit_id)


# ── 自主执行循环 ──

class _PursuitCancelled(Exception):
    pass


class _PursuitTimedOut(Exception):
    pass


def _check(cancel_event, deadline):
    if cancel_event and cancel_event.is_set():
        raise _PursuitCancelled
    if time.monotonic() >= deadline:
        raise _PursuitTimedOut


def _llm_json(messages: list[dict], cancel_event=None, deadline: float = float("inf")) -> dict:
    """调用 LLM 并解析 JSON 输出。"""
    messages = [dict(message) for message in messages]
    messages[0]["content"] += "\n分析、任务说明、总结等自然语言字段必须用中文；代码、路径和 JSON 字段名保留原文。"
    raw = llm.chat_text(
        messages,
        timeout=min(PURSUIT_LLM_TIMEOUT, max(5, deadline - time.monotonic())),
        cancel_event=cancel_event,
    )
    _check(cancel_event, deadline)
    # 清理 markdown 代码块
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 尝试找到 JSON 块
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {"error": "JSON 解析失败", "raw": raw[:500]}


def _format_plan(plan: list[dict]) -> str:
    lines = []
    for step in plan:
        status_icon = {"pending": "⬜", "running": "🔄", "completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(step.get("status", "pending"), "❓")
        lines.append(f"{status_icon} #{step.get('id', '?')} {step.get('task', '?')}")
    return "\n".join(lines)


def _get_current_step(plan: list[dict]) -> dict | None:
    """获取下一个待执行的步骤。"""
    for step in plan:
        if step.get("status") == "pending":
            # 检查依赖是否都已完成
            deps = step.get("depends_on", [])
            if all(
                any(s["id"] == d and s["status"] in ("completed", "skipped") for s in plan)
                for d in deps
            ):
                return step
    return None


def run_pursuit(
    pursuit_id: str,
    session_id: str,
    cancel_event: threading.Event | None = None,
):
    """自主追求目标的主循环。生成 SSE 事件。"""
    _ensure_table()
    pursuit = get_pursuit(pursuit_id)
    if not pursuit:
        yield {"type": "error", "message": "目标不存在"}
        return

    update_pursuit(pursuit_id, status="running")
    goal = pursuit["goal"]
    deadline = time.monotonic() + PURSUIT_SAFETY_TIMEOUT
    trace = Trace(session_id)

    try:
        yield {"type": "pursuit_start", "pursuit_id": pursuit_id, "goal": goal}
        # ── Phase 1: 规划 ──
        yield {"type": "pursuit_phase", "phase": "planning", "message": "🧠 正在分析目标并制定计划…"}

        reset_session_context(session_id)
        set_run_control(cancel_event, deadline)
        _check(cancel_event, deadline)
        user_id = memory.save_message(session_id, "user", goal)
        yield {"type": "user_id", "message_id": user_id, "run_id": trace.run_id}

        plan_data = _llm_json(
            [
                {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": f"目标：{goal}\n\n请拆解为可执行的子任务计划。"},
            ],
            cancel_event=cancel_event,
            deadline=deadline,
        )

        _check(cancel_event, deadline)

        if "error" in plan_data:
            update_pursuit(pursuit_id, status="failed", summary=f"规划失败：{plan_data.get('raw', '')[:200]}")
            yield {"type": "error", "message": "计划制定失败，请换个方式描述目标"}
            return

        plan = plan_data.get("plan", [])
        if not plan:
            update_pursuit(pursuit_id, status="failed", summary="未能拆解出子任务")
            yield {"type": "error", "message": "目标可能太模糊了，能再具体一些吗？"}
            return

        # 给每个步骤设置初始状态
        for step in plan:
            step.setdefault("status", "pending")
            step.setdefault("depends_on", [])

        update_pursuit(pursuit_id, plan_json=json.dumps(plan, ensure_ascii=False))
        for step in plan:
            save_step(pursuit_id, step["id"], step["task"], "pending")

        yield {
            "type": "pursuit_plan",
            "analysis": plan_data.get("analysis", ""),
            "plan": plan,
            "formatted": _format_plan(plan),
        }

        # ── Phase 2: 逐步执行 ──
        round_count = 0
        last_eval_progress = 0
        stuck_count = 0

        while round_count < PURSUIT_MAX_ROUNDS:
            _check(cancel_event, deadline)
            round_count += 1

            current = _get_current_step(plan)
            if not current:
                # 所有步骤都完成了，做最终评估
                break

            step_id = current["id"]
            current["status"] = "running"
            save_step(pursuit_id, step_id, current["task"], "running")
            update_pursuit(pursuit_id, plan_json=json.dumps(plan, ensure_ascii=False))

            yield {
                "type": "pursuit_step_start",
                "step_id": step_id,
                "task": current["task"],
                "formatted": _format_plan(plan),
            }

            # 构建执行上下文
            completed_results = []
            for s in plan:
                if s["status"] == "completed" and s.get("result"):
                    completed_results.append(f"#{s['id']} {s['task']}: {s['result'][:200]}")

            exec_context = f"最终目标：{goal}\n\n当前子任务 #{step_id}：{current['task']}"
            if completed_results:
                exec_context += "\n\n已完成的步骤结果：\n" + "\n".join(completed_results)

            # 执行子任务（多轮工具调用）
            tool_messages = []
            step_completed = False
            step_result = ""
            step_deliverables = []

            for tool_round in range(100):  # 每个子任务最多 100 轮工具调用
                _check(cancel_event, deadline)

                messages = [
                    {"role": "system", "content": _EXEC_SYSTEM_PROMPT + "\n解释与 result 使用中文，不因英文代码或工具结果切换语言。"},
                ]
                # 加上之前的工具调用历史
                messages.append({"role": "user", "content": exec_context})
                messages.extend(tool_messages)

                content_parts = []
                tool_calls = {}
                reply_text = ""

                for ev in trace.stream(
                    llm.chat_stream,
                    messages,
                    tools=TOOL_SCHEMAS,
                    timeout=min(PURSUIT_LLM_TIMEOUT, max(5, deadline - time.monotonic())),
                    cancel_event=cancel_event,
                ):
                    _check(cancel_event, deadline)
                    if ev["kind"] == "content":
                        content_parts.append(ev["text"])
                        reply_text += ev["text"]
                    elif ev["kind"] == "tool_delta":
                        call = tool_calls.setdefault(
                            ev["index"], {"id": "", "name": "", "arguments": ""}
                        )
                        if ev["id"]:
                            call["id"] = ev["id"]
                        if ev["name"]:
                            call["name"] = ev["name"]
                        if ev["arguments"]:
                            call["arguments"] += ev["arguments"]

                full_content = "".join(content_parts)

                # 检查是否有工具调用
                if tool_calls:
                    calls = []
                    for idx in sorted(tool_calls):
                        tc = tool_calls[idx]
                        calls.append({
                            "id": tc["id"] or f"call_{idx}",
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        })

                    tool_messages.append({
                        "role": "assistant",
                        "content": full_content or None,
                        "tool_calls": calls,
                    })

                    for call in calls:
                        try:
                            fn_args = json.loads(call["function"]["arguments"] or "{}")
                        except json.JSONDecodeError:
                            fn_args = {}
                        tool_name = call["function"]["name"]

                        yield {
                            "type": "pursuit_tool",
                            "step_id": step_id,
                            "name": tool_name,
                            "desc": describe_tool(tool_name, fn_args),
                            "detail": (str(fn_args.get("cwd") or session_work_root(session_id, create=False)) + " · " + str(fn_args.get("command") or "")) if tool_name == "run_code" else describe_tool(tool_name, fn_args),
                        }

                        # 目标执行遵循当前会话的权限与审批规则。
                        _check(cancel_event, deadline)
                        set_session_context(session_id)
                        set_approved_context(False)
                        need, desc = approval_needed(tool_name, fn_args)
                        review = None
                        if not need:
                            review = review_agent.review_tool_call(session_id, tool_name, fn_args, describe_tool(tool_name, fn_args))
                            if review["approved"] is None:
                                need, desc = True, "AI 审查建议：" + review["reason"]
                        decision = None
                        if need:
                            rememberable = approval_rememberable(tool_name)
                            decision = yield {"type": "approval", "request_id": "ap-" + uuid.uuid4().hex[:10],
                                              "name": tool_name, "desc": desc, "remember": rememberable}
                            if decision == "allow_always" and rememberable:
                                record_approval_rule(tool_name)
                        _check(cancel_event, deadline)
                        if review and review["approved"] is False:
                            result = "审查拒绝了这次操作：" + review["reason"]
                        elif need and decision not in ("allow", "allow_always"):
                            result = f"用户拒绝了这次操作（{desc}），不要执行。"
                        else:
                            set_session_context(session_id)
                            set_approved_context(need or auto_approved(tool_name))
                            try:
                                result = trace.call(execute_tool, tool_name, fn_args)
                            finally:
                                set_approved_context(False)

                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": result,
                        })

                        yield {
                            "type": "pursuit_tool_result",
                            "step_id": step_id,
                            "name": tool_name,
                            "summary": result[:160],
                        }
                else:
                    # 没有工具调用，尝试解析执行结果
                    try:
                        # 尝试从回复中提取 JSON
                        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", full_content.strip(), flags=re.MULTILINE).strip()
                        json_start = cleaned.find("{")
                        json_end = cleaned.rfind("}")
                        if json_start != -1 and json_end > json_start:
                            exec_result = json.loads(cleaned[json_start:json_end + 1])
                            status = exec_result.get("status", "completed")
                            step_result = exec_result.get("result", full_content[:500])
                            step_deliverables = exec_result.get("deliverables", [])
                            step_completed = status == "completed"
                            if status == "needs_input":
                                current["status"] = "pending"
                                save_step(pursuit_id, step_id, current["task"], "pending", step_result)
                                update_pursuit(pursuit_id, status="paused", summary=step_result,
                                               plan_json=json.dumps(plan, ensure_ascii=False))
                                yield {
                                    "type": "pursuit_needs_input",
                                    "step_id": step_id,
                                    "message": step_result,
                                }
                                reply_id = memory.save_message(session_id, "assistant", "需要你补充：" + step_result)
                                yield {"type": "done", "message_id": reply_id, "run_id": trace.run_id}
                                return
                            break
                    except (json.JSONDecodeError, AttributeError):
                        pass

                    # 如果没有 JSON 但有内容，认为是完成
                    if full_content.strip():
                        step_result = full_content[:500]
                        step_completed = True
                        break

            # 更新步骤状态
            if step_completed:
                current["status"] = "completed"
                current["result"] = step_result
                save_step(pursuit_id, step_id, current["task"], "completed",
                          step_result, step_deliverables)
                yield {
                    "type": "pursuit_step_done",
                    "step_id": step_id,
                    "result": step_result[:300],
                    "deliverables": step_deliverables,
                    "formatted": _format_plan(plan),
                }
            else:
                current["status"] = "failed"
                current["result"] = step_result or "执行超时或未完成"
                save_step(pursuit_id, step_id, current["task"], "failed",
                          current["result"])
                yield {
                    "type": "pursuit_step_failed",
                    "step_id": step_id,
                    "result": current["result"][:300],
                    "formatted": _format_plan(plan),
                }
                stuck_count += 1
                if stuck_count >= 3:
                    yield {"type": "pursuit_stuck", "message": "连续多个步骤失败，可能需要调整计划"}

            update_pursuit(pursuit_id, plan_json=json.dumps(plan, ensure_ascii=False))

            # 每完成一个步骤做一次快速评估（可选，每 3 步或有失败时）
            if step_completed and (round_count % 3 == 0 or stuck_count > 0):
                _check(cancel_event, deadline)
                eval_data = _llm_json(
                    [
                        {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
                        {"role": "user", "content": (
                            f"最终目标：{goal}\n\n"
                            f"当前计划：\n{_format_plan(plan)}\n\n"
                            f"请评估整体进展。"
                        )},
                    ],
                    cancel_event=cancel_event,
                    deadline=deadline,
                )

                if "error" not in eval_data:
                    progress = eval_data.get("progress_pct", last_eval_progress)
                    update_pursuit(
                        pursuit_id,
                        progress_pct=progress,
                        summary=eval_data.get("summary", ""),
                    )
                    last_eval_progress = progress
                    yield {
                        "type": "pursuit_progress",
                        "progress_pct": progress,
                        "summary": eval_data.get("summary", ""),
                        "formatted": _format_plan(plan),
                    }

                    # 如果评估认为需要修改计划
                    revision = eval_data.get("revision")
                    if revision and isinstance(revision, dict):
                        for add_task in revision.get("add", []):
                            new_id = max(s["id"] for s in plan) + 1 if plan else 1
                            new_step = {
                                "id": new_id,
                                "task": add_task.get("task", ""),
                                "depends_on": add_task.get("depends_on", []),
                                "status": "pending",
                            }
                            plan.append(new_step)
                            save_step(pursuit_id, new_id, new_step["task"], "pending")
                        for remove_id in revision.get("remove", []):
                            for s in plan:
                                if s["id"] == remove_id and s["status"] == "pending":
                                    s["status"] = "skipped"
                                    save_step(pursuit_id, remove_id, s["task"], "skipped")
                        for mod in revision.get("modify", []):
                            for s in plan:
                                if s["id"] == mod.get("id") and s["status"] == "pending":
                                    s["task"] = mod.get("task", s["task"])
                                    save_step(pursuit_id, s["id"], s["task"], "pending")

                        update_pursuit(pursuit_id, plan_json=json.dumps(plan, ensure_ascii=False))
                        if revision.get("add") or revision.get("remove") or revision.get("modify"):
                            yield {
                                "type": "pursuit_plan_revised",
                                "formatted": _format_plan(plan),
                            }

        # ── Phase 3: 最终评估 ──
        _check(cancel_event, deadline)

        final_eval = _llm_json(
            [
                {"role": "system", "content": _EVAL_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"最终目标：{goal}\n\n"
                    f"最终计划与执行结果：\n{json.dumps(plan, ensure_ascii=False)}\n\n"
                    f"请做最终评估，总结交付物。"
                )},
            ],
            cancel_event=cancel_event,
            deadline=deadline,
        )

        _check(cancel_event, deadline)
        completed = final_eval.get("overall_status") == "completed" and all(
            step["status"] in ("completed", "skipped") for step in plan)
        final_summary = final_eval.get("deliverable_summary") or final_eval.get("summary") or "未能确认目标完成，请检查执行结果。"
        update_pursuit(pursuit_id, status="completed" if completed else "failed",
                       progress_pct=100 if completed else last_eval_progress,
                       summary=final_summary, finished_at=time.time())
        trace.status = "completed" if completed else "error"
        if not completed:
            yield {"type": "error", "message": final_summary}
            return
        reply = final_summary + "\n\n" + _format_plan(plan)
        reply_id = memory.save_message(session_id, "assistant", reply)

        yield {
            "type": "pursuit_complete",
            "pursuit_id": pursuit_id,
            "summary": final_summary or "目标追求完成！",
            "formatted": _format_plan(plan),
        }
        yield {"type": "done", "message_id": reply_id, "run_id": trace.run_id}

    except GeneratorExit:
        trace.status = "cancelled"
        update_pursuit(pursuit_id, status="stopped", finished_at=time.time())
        raise
    except _PursuitCancelled:
        trace.status = "cancelled"
        update_pursuit(pursuit_id, status="stopped", finished_at=time.time())
        yield {"type": "pursuit_stopped", "message": "追求已停止"}
    except _PursuitTimedOut:
        trace.status = "timeout"
        update_pursuit(pursuit_id, status="failed", summary="安全超时", finished_at=time.time())
        yield {"type": "error", "message": f"追求超时（{PURSUIT_SAFETY_TIMEOUT // 60} 分钟安全限制）"}
    except Exception as e:
        trace.status = "error"
        update_pursuit(pursuit_id, status="failed", summary=str(e)[:200], finished_at=time.time())
        yield {"type": "error", "message": f"追求出错：{str(e)[:200]}"}
    finally:
        if trace.status == "running":
            trace.status = "paused" if get_pursuit(pursuit_id)["status"] == "paused" else "error"
        trace.finish()
        cleanup_session_temp(session_id)
        clear_session_context()
