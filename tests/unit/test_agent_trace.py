from __future__ import annotations

from app.publishing.agent_trace import AgentTraceState, build_step_trace


def test_step_trace_extracts_agent_thinking_and_search_intent() -> None:
    state = AgentTraceState()

    trace = build_step_trace(
        step_number=1,
        model_output={
            "thinking": "I need to find the category selector before publishing.",
            "evaluation_previous_goal": "Title and body are ready.",
            "memory": "Cover image is uploaded.",
            "next_goal": "Find 分类 selector or 发文设置.",
            "action": [
                {
                    "find_elements": {
                        "selector": "select, [class*='分类']",
                        "max_results": 20,
                    }
                }
            ],
        },
        results=[
            {
                "extracted_content": 'No elements found matching "select, [class*=分类]".',
            }
        ],
        page_state={"url": "https://mp.toutiao.com/publish", "probe_found": True},
        duration_seconds=1.25,
        state=state,
    )

    assert trace.action_type == "find_elements"
    assert trace.intent == "查找分类/发文设置"
    assert 'selector="select, [class*=\'分类\']"' in trace.search_line
    assert "previous=\"Title and body are ready.\"" in trace.think_line
    assert "duration=1.25s" in trace.step_line


def test_step_trace_warns_about_scroll_loop_without_progress() -> None:
    state = AgentTraceState(repeat_window=8)

    warnings = []
    for step in range(1, 5):
        trace = build_step_trace(
            step_number=step,
            model_output={
                "thinking": "Still looking for the category selector.",
                "next_goal": "Find 分类 selector.",
                "action": [{"scroll": {"down": False, "pages": 1.5}}],
            },
            results=[{"extracted_content": "Scrolled up 1.5 pages"}],
            page_state={
                "url": "https://mp.toutiao.com/publish",
                "editor_text_length": 662,
                "probe_found": True,
            },
            duration_seconds=7.0,
            state=state,
        )
        warnings.extend(trace.repeat_warnings)

    assert any("pattern=scroll_loop" in warning for warning in warnings)
    assert any("intent=\"查找分类/发文设置\"" in warning for warning in warnings)


def test_step_trace_warns_about_same_click() -> None:
    state = AgentTraceState()

    first = build_step_trace(
        step_number=1,
        model_output={
            "next_goal": "Confirm cover image.",
            "action": [{"click": {"index": 3580}}],
        },
        results=[{"extracted_content": 'Clicked button "确定"'}],
        page_state={"url": "https://mp.toutiao.com/publish"},
        duration_seconds=1.0,
        state=state,
    )
    second = build_step_trace(
        step_number=2,
        model_output={
            "next_goal": "Confirm cover image.",
            "action": [{"click": {"index": 3580}}],
        },
        results=[{"extracted_content": 'Clicked button "确定"'}],
        page_state={"url": "https://mp.toutiao.com/publish"},
        duration_seconds=1.0,
        state=state,
    )

    assert first.repeat_warnings == []
    assert any("pattern=same_click" in warning for warning in second.repeat_warnings)
    assert any("target=\"click:index=3580\"" in warning for warning in second.repeat_warnings)
