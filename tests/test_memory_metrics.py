from datetime import UTC, datetime, timedelta

from app.core.memory import MemoryStore


def test_memory_metrics_summary_and_state(tmp_path):
    db_path = tmp_path / "jarvis_metrics.db"
    memory = MemoryStore(str(db_path))

    memory.set_state("runtime_context", {"active_surface": "youtube"})
    context = memory.get_state("runtime_context")
    assert context["active_surface"] == "youtube"

    memory.log_execution_metric(
        source="cli",
        command="youtube next",
        status="completed",
        duration_ms=120,
        actions_total=1,
        actions_completed=1,
        actions_failed=0,
        actions_pending=0,
    )
    memory.log_execution_metric(
        source="cli",
        command="send whatsapp to 123: hi",
        status="failed",
        duration_ms=340,
        actions_total=1,
        actions_completed=0,
        actions_failed=1,
        actions_pending=0,
    )

    summary = memory.metrics_summary(limit=50)
    assert summary["total_commands"] == 2
    assert summary["status_counts"]["completed"] == 1
    assert summary["status_counts"]["failed"] == 1


def test_session_review_since(tmp_path):
    db_path = tmp_path / "review.db"
    memory = MemoryStore(str(db_path))
    before = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()

    memory.log_command(source="cli", command="open chrome", response_text="Opened chrome.")
    memory.log_tool_result(command="open chrome", tool="open_application", status="completed", risk="low", output={"target": "chrome"})
    memory.log_job_application(site="linkedin", title="Engineer", company="Acme", location=None, url="http://x", status="applied")

    review = memory.session_review(since=before)
    assert review["command_count"] == 1
    assert review["sources"].get("cli") == 1
    assert review["tool_counts"].get("completed") == 1
    assert review["job_status"].get("applied") == 1

    later = datetime.now(UTC).isoformat()
    review_after = memory.session_review(since=later)
    assert review_after["command_count"] == 0
