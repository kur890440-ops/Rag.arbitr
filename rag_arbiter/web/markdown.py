"""Render model prose, never model-supplied HTML or remote images."""
from markdown_it import MarkdownIt
from markupsafe import Markup

_renderer = MarkdownIt('commonmark', {'html': False, 'breaks': True}).enable('table').disable('image')


def answer_markdown(text):
    return Markup(_renderer.render(str(text or '')))
