from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from kaoyan.agent.nodes import (
    act_node,
    after_reflect,
    finalize_node,
    plan_node,
    reflect_node,
    route_node,
)
from kaoyan.agent.state import AgentState
from kaoyan.memory import TaskRecord, get_session_memory, get_task_store, new_task_id


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("plan", plan_node)
    g.add_node("act", act_node)
    g.add_node("reflect", reflect_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", route_node, {"act": "act", "finalize": "finalize"})
    g.add_edge("act", "reflect")
    g.add_conditional_edges(
        "reflect", after_reflect, {"act": "act", "finalize": "finalize"}
    )
    g.add_edge("finalize", END)
    return g.compile()


@lru_cache
def get_graph():
    return build_graph()


def token_usage(messages: list) -> dict[str, int]:
    """Sum LangChain usage_metadata. Messages without usage count as zero."""
    prompt = completion = calls = 0
    for msg in messages or []:
        meta = getattr(msg, "usage_metadata", None)
        if not isinstance(meta, dict):
            continue
        inp = int(meta.get("input_tokens") or 0)
        out = int(meta.get("output_tokens") or 0)
        if inp or out:
            calls += 1
            prompt += inp
            completion += out
    return {
        "llm_calls_with_usage": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def run_agent(
    message: str,
    session_id: str | None = None,
    *,
    task_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    sid = session_id or str(uuid4())
    tid = task_id or new_task_id()
    graph = get_graph()
    initial: AgentState = {
        "messages": [HumanMessage(content=message)],
        "user_goal": message,
        "session_id": sid,
        "task_id": tid,
        "plan": [],
        "route": "",
        "tool_results": [],
        "citations": [],
        "exports": [],
        "reflection": "",
        "should_retry": False,
        "retry_hint": "",
        "iteration": 0,
        "final_answer": "",
        "status": "started",
        "error": "",
        "intent": {},
        "facts": {},
    }
    final = graph.invoke(initial)

    answer = final.get("final_answer") or ""
    plan = final.get("plan") or []
    citations = final.get("citations") or []
    trace = final.get("tool_results") or []
    exports = final.get("exports") or []
    reflection = final.get("reflection") or ""
    status = final.get("status") or ""
    iterations = final.get("iteration") or 0

    if persist:
        session_mem = get_session_memory()
        session_mem.append(sid, "user", message)
        session_mem.append(sid, "assistant", answer[:2000])

        store = get_task_store()
        existing = store.get_task(tid)
        created_at = (
            existing.created_at
            if existing and existing.created_at
            else datetime.now(timezone.utc).isoformat()
        )
        record = TaskRecord(
            task_id=tid,
            session_id=sid,
            user_goal=message,
            plan=list(plan),
            answer=answer,
            citations=list(citations)[:20],
            trace=list(trace),
            reflection=reflection,
            exports=list(exports),
            status=status or "done",
            iterations=int(iterations),
            created_at=created_at,
        )
        store.save_task(record)
        store.append_turn(sid, "user", message, task_id=tid)
        store.append_turn(sid, "assistant", answer[:4000], task_id=tid)
        if final.get("intent"):
            store.save_slots(sid, final["intent"])

    return {
        "session_id": sid,
        "task_id": tid,
        "answer": answer,
        "plan": plan,
        "citations": citations,
        "trace": trace,
        "exports": exports,
        "reflection": reflection,
        "status": status,
        "iterations": iterations,
        "intent": final.get("intent") or {},
        "facts": final.get("facts") or {},
        "usage": token_usage(final.get("messages") or []),
    }
