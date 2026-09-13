from interface.test_legal_agreement import _load_app


def test_portal_and_chat_routes_serve_one_frontend_without_auth(tmp_path, monkeypatch):
    client, _, _, _, provision_calls = _load_app(tmp_path, monkeypatch)
    try:
        responses = [client.get(path) for path in ("/", "/lite", "/chat")]
        assert all(response.status_code == 200 for response in responses)
        assert all(response.content == responses[0].content for response in responses)
        assert all("text/html" in response.headers["content-type"] for response in responses)
        assert provision_calls == []
    finally:
        client.close()
