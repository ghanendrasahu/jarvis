from app.core.events import EventHub


def test_eventhub_publish_and_unsubscribe():
    hub = EventHub()
    seen = []

    unsub = hub.subscribe(lambda topic, payload: seen.append((topic, payload.get("x"))))
    hub.publish("test", {"x": 1})
    assert seen == [("test", 1)]

    unsub()
    hub.publish("test", {"x": 2})
    assert seen == [("test", 1)]


def test_eventhub_isolates_listener_errors():
    hub = EventHub()
    seen = []

    def bad(_topic, _payload):
        raise RuntimeError("boom")

    hub.subscribe(bad)
    hub.subscribe(lambda topic, payload: seen.append(topic))
    hub.publish("ok", {})
    assert seen == ["ok"]
