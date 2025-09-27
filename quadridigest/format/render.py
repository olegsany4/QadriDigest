
from jinja2 import Template
import yaml
from pathlib import Path

class TemplateRenderer:
    def __init__(self, path: str = "quadridigest/format/templates.yaml"):
        text = Path(path).read_text(encoding="utf-8")
        self.tpl = yaml.safe_load(text)

    def render_short(self, **kwargs) -> str:
        return Template(self.tpl["short"]).render(**kwargs).strip()

    def render_full(self, **kwargs) -> str:
        return Template(self.tpl["full"]).render(**kwargs).strip()
