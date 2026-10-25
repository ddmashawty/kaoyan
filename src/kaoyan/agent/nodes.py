from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from kaoyan.agent import kaoyan_flow
from kaoyan.agent.guardrails import detect_kaoyan_intent, recent_tools
from kaoyan.agent.state import AgentState
from kaoyan.llm.factory import get_chat_model
from kaoyan.memory import get_session_memory, load_session_slots
from kaoyan.runtime_options import effective_max_tool_calls
from kaoyan.tools import kaoyan as kaoyan_tools
from kaoyan.tools.registry import parse_export_payload, tools_by_name


def _run_tool(
    tool_map: dict[str, Any],
    name: str,
    args: dict[str, Any],
    results: list[dict[str, Any]],
    messages: list[Any],
    citations: list[dict[str, Any]],
    exports: list[dict[str, Any]],
    *,
    tool_call_id: str | None = None) -> None:
    tool = tool_map.get(name)
    try:
        output = tool.invoke(args) if tool else f"未知工具: {name}"
    except Exception as exc:  # noqa: BLE001
        output = f"工具执行失败: {exc}"
    results.append({"tool": name, "args": args, "output": output})
    # Only attach ToolMessage when paired with an LLM tool_call id.
    if tool_call_id:
        messages.append(ToolMessage(content=str(output), tool_call_id=tool_call_id))
    if name == "rag_search":
        try:
            parsed = json.loads(str(output))
            if isinstance(parsed, list):
                citations.extend(parsed)
        except json.JSONDecodeError:
            pass
    citations.extend(kaoyan_tools.citations_from_output(name, output))
    export_meta = parse_export_payload(name, output)
    if export_meta:
        exports.append(export_meta)


def plan_node(state: AgentState) -> dict[str, Any]:
    sid = state.get("session_id") or ""
    intent = detect_kaoyan_intent(state.get("user_goal") or "", previous=load_session_slots(sid))
    if sid:
        get_session_memory().set_slots(sid, intent)
    intent["available"] = bool(intent["is_kaoyan"]) and kaoyan_tools.kaoyan_available()
    if not intent["available"]:
        return {
            "plan": [],
            "route": "direct",
            "intent": intent,
            "status": "planned",
            "messages": [AIMessage(content=f"规划完成: route=direct; is_kaoyan={intent['is_kaoyan']}")],
        }
    plan = kaoyan_flow.plan_steps(intent)
    return {
        "plan": plan,
        "route": "tools",
        "intent": intent,
        "status": "planned",
        "messages": [AIMessage(content=f"规划完成(考研): kinds={intent['kinds']}; plan={plan}")],
    }


def route_node(state: AgentState) -> str:
    if state.get("route") == "direct":
        return "finalize"
    if state.get("iteration", 0) >= effective_max_tool_calls():
        return "finalize"
    return "act"


def act_node(state: AgentState) -> dict[str, Any]:
    tool_map = tools_by_name()
    results = list(state.get("tool_results") or [])
    citations = list(state.get("citations") or [])
    exports = list(state.get("exports") or [])
    messages: list[Any] = []
    iteration = state.get("iteration", 0)

    def run(name: str, args: dict[str, Any]) -> None:
        _run_tool(tool_map, name, args, results, messages, citations, exports)

    if iteration == 0:
        kaoyan_flow.bootstrap(dict(state), run)
        if state["intent"].get("export"):
            kaoyan_flow.export(dict(state), results, run)
    else:
        kaoyan_flow.continue_act(dict(state), results, exports, run)
    return {
        "messages": messages + [AIMessage(content=f"考研工具: {recent_tools(results, 8)}")],
        "tool_results": results,
        "citations": citations,
        "exports": exports,
        "iteration": iteration + 1,
        "should_retry": False,
        "status": "acted",
    }


def reflect_node(state: AgentState) -> dict[str, Any]:
    if state.get("iteration", 0) >= effective_max_tool_calls():
        return {
            "reflection": "已达工具调用上限，停止重试。",
            "should_retry": False,
            "retry_hint": "",
            "status": "reflected",
        }
    return kaoyan_flow.reflect(dict(state))


def after_reflect(state: AgentState) -> str:
    if state.get("should_retry") and state.get("iteration", 0) < effective_max_tool_calls():
        return "act"
    return "finalize"


def finalize_node(state: AgentState) -> dict[str, Any]:
    if state.get("route") == "direct":
        answer = kaoyan_flow.direct_reply(state.get("intent") or {})
        return {"final_answer": answer, "status": "done", "messages": [AIMessage(content=answer)]}
    answer, validation = kaoyan_flow.finalize(dict(state), get_chat_model())
    exports = state.get("exports") or []
    paths = ", ".join(str(e.get("path")) for e in exports if e.get("path"))
    if paths and "data/exports" not in answer:
        answer = answer.rstrip() + f"\n\n**导出文件：** {paths}"
    return {
        "final_answer": answer,
        "facts": {**(state.get("facts") or {}), "validation": validation},
        "status": "done",
        "messages": [AIMessage(content=answer)],
    }
