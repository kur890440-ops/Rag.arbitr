from rag_arbiter.web.markdown import answer_markdown


def test_prose_and_citations():
    html=str(answer_markdown('# Title\n\n**Strong** [S1] [PAGE 1]\n\n- first\n- second\n\n| A | B |\n|---|---|\n|1|2|'))
    assert '<h1>Title</h1>' in html and '<strong>Strong</strong>' in html
    assert '<ul>' in html and '<table>' in html
    assert '[S1]' in html and '[PAGE 1]' in html


def test_untrusted_html_urls_and_images():
    html=str(answer_markdown('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert(1))\n\n![remote](https://example.com/tracker.png)'))
    assert '<script' not in html and '<img' not in html
    assert 'href="javascript:' not in html
    assert '&lt;script&gt;' in html
