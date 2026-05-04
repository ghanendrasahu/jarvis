from app.core.router import DirectCommandRouter


def test_open_command_routes_to_open_application():
    router = DirectCommandRouter()
    actions = router.route("open chrome")
    assert len(actions) == 1
    assert actions[0].tool == "open_application"
    assert actions[0].args["name"] == "chrome"


def test_shell_command_routes_to_guarded_shell_tool():
    router = DirectCommandRouter()
    actions = router.route("run Get-Date")
    assert len(actions) == 1
    assert actions[0].tool == "run_shell_command"
    assert actions[0].risk == "high"


def test_play_youtube_query_routes_to_video_tool():
    router = DirectCommandRouter()
    actions = router.route("play lofi hip hop on youtube")
    assert len(actions) == 1
    assert actions[0].tool == "open_youtube_video"
    assert actions[0].args["query"] == "lofi hip hop"


def test_youtube_next_routes_to_control_tool():
    router = DirectCommandRouter()
    actions = router.route("youtube next")
    assert len(actions) == 1
    assert actions[0].tool == "control_youtube"
    assert actions[0].args["action"] == "next_video"


def test_whatsapp_send_routes_to_chat_tool():
    router = DirectCommandRouter()
    actions = router.route("send whatsapp to +91 9876543210: hello from jarvis")
    assert len(actions) == 1
    assert actions[0].tool == "open_whatsapp_chat"
    assert actions[0].args["phone"] == "+91 9876543210"
    assert actions[0].args["message"] == "hello from jarvis"


def test_typo_tolerant_youtube_routing():
    router = DirectCommandRouter()
    actions = router.route("play synthwave mix on youutube")
    assert len(actions) == 1
    assert actions[0].tool == "open_youtube_video"
    assert actions[0].args["query"] == "synthwave mix"


def test_context_followup_routes_to_youtube_control():
    router = DirectCommandRouter()
    actions = router.route("next", context={"active_surface": "youtube"})
    assert len(actions) == 1
    assert actions[0].tool == "control_youtube"
    assert actions[0].args["action"] == "next_video"


def test_list_shortcuts_routes_to_discovery_tool():
    router = DirectCommandRouter()
    actions = router.route("list shortcuts")
    assert len(actions) == 1
    assert actions[0].tool == "list_app_shortcuts"
