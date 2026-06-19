"""Sphinx configuration for the yuj documentation."""

from __future__ import annotations

from pygments.lexers.special import TextLexer
from sphinx.highlighting import lexers

project = "yuj"
author = "Saket Choudhary"
copyright = "2026 Saket Choudhary — MIT License"


extensions = [
    "myst_parser",
    "sphinx_design",
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",
]

source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "testing.md"]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "attrs_inline",
    "substitution",
    "tasklist",
]
myst_fence_as_directive = ["mermaid"]
myst_heading_anchors = 3

lexers["csv"] = TextLexer()


html_theme = "furo"
html_title = "yuj"
html_static_path = ["_static"]
html_css_files = ["custom.css"]

_SF_TEXT = (
    '-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", '
    '"Helvetica Neue", Helvetica, Arial, sans-serif'
)
_SF_MONO = (
    '"SF Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, '
    '"Cascadia Code", "Roboto Mono", monospace'
)

html_theme_options = {
    "light_css_variables": {
        "font-stack": _SF_TEXT,
        "font-stack--monospace": _SF_MONO,
        "color-brand-primary": "#0066cc",
        "color-brand-content": "#0066cc",
        "color-background-primary": "#ffffff",
        "color-background-secondary": "#f5f5f7",
        "color-foreground-primary": "#1d1d1f",
        "color-foreground-secondary": "#6e6e73",
        "color-foreground-muted": "#6e6e73",
        "color-foreground-border": "rgba(0, 0, 0, 0.09)",
        "color-background-border": "rgba(0, 0, 0, 0.09)",
    },
    "dark_css_variables": {
        "font-stack": _SF_TEXT,
        "font-stack--monospace": _SF_MONO,
        "color-brand-primary": "#2997ff",
        "color-brand-content": "#2997ff",
        "color-background-primary": "#000000",
        "color-background-secondary": "#1c1c1e",
        "color-foreground-primary": "#f5f5f7",
        "color-foreground-secondary": "#86868b",
        "color-foreground-muted": "#86868b",
        "color-foreground-border": "rgba(255, 255, 255, 0.1)",
        "color-background-border": "rgba(255, 255, 255, 0.1)",
    },
    "source_repository": "https://github.com/saketlab/yuj",
    "source_branch": "main",
    "source_directory": "docs/",
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/saketlab/yuj",
            "html": (
                '<svg stroke="currentColor" fill="currentColor" stroke-width="0" '
                'viewBox="0 0 16 16"><path fill-rule="evenodd" d="M8 0C3.58 0 0 '
                "3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 "
                "0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94"
                "-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 "
                "1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 "
                "0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82."
                "64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82"
                ".44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 "
                "3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38"
                'A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path></svg>'
            ),
            "class": "",
        },
    ],
}
