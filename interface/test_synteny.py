from fastapi.testclient import TestClient

from interface import app as interface_app_mod


def test_synteny_page_and_plot_are_public():
    # Do not enter the lifespan: serving this page needs no databases or runtime.
    client = TestClient(interface_app_mod.app)
    try:
        response = client.get('/synteny', follow_redirects=False)
        assert response.status_code == 200
        assert 'data-portal-module="synteny"' in response.text
        assert 'Ask Potato Agent' in response.text
        assert client.head('/synteny').status_code == 200
        for name, content_type in (
            ('styles.css', 'text/css'),
            ('app.js', 'text/javascript'),
            ('assets/synteny_plot.png', 'image/png'),
        ):
            asset = client.get('/static/synteny/' + name)
            assert asset.status_code == 200
            assert asset.headers['content-type'].startswith(content_type)
    finally:
        client.close()
