from app.core.approvals import ApprovalManager
from app.models.schemas import Action


def test_approval_create_and_approve_cycle():
    approvals = ApprovalManager(ttl_minutes=5)
    approval_id = approvals.create(
        command="close chrome",
        action=Action(tool="close_application", args={"name": "chrome"}, risk="medium", reason="close app"),
    )
    pending = approvals.list_pending()
    assert any(item.approval_id == approval_id for item in pending)

    approved = approvals.approve(approval_id)
    assert approved is not None
    assert approved.approval_id == approval_id

