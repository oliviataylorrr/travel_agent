from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'templates' / 'agent.html'

def test_operator_chat_ui_is_conversational():
    html = TEMPLATE.read_text(encoding='utf-8')
    for marker in ('chat-thread', 'chat-form', 'chat-composer', 'message-label', 'role', 'csrf', 'Send message'):
        assert marker in html
    assert "new FormData(form)" in html
    assert "fetch(form.action" in html
    assert 'DOMParser' in html
    assert 'window.location.href' in html

def test_side_panel_and_css_exist():
    html = TEMPLATE.read_text(encoding='utf-8')
    css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
    assert 'workspace-grid' in html and 'aside' in html and 'metrics' in html
    assert '@media(max-width:980px)' in css

def test_operator_identity_instruction_is_explicit():
    source = (ROOT / 'app' / 'main.py').read_text(encoding='utf-8')
    assert 'not a travel client' in source.lower() or 'not a travel customer' in source.lower() or 'not a travel client' in (TEMPLATE.read_text().lower())
