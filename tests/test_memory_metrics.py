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
