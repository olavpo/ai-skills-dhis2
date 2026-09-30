"""Minimal inline-SVG builder for report diagrams (workflow lanes, program
structures, options side by side).

    from svg_diagram import Svg, figure, DIAGRAM_CSS
    s = Svg("wf", 800, 260, "Workflow: enumerator lists the household, school follows up")
    s.lane(10, 20, 780, 100, "Enumerator", "day 0")
    s.box(200, 45, 160, 52, "List household", ["roster of every child"], center=True)
    s.box(480, 45, 160, 52, "Register case", ["linked tracked entity"], cls="bx hl", center=True)
    s.path("M360 71 H480")
    html = figure(s, "Assumed workflow")

The markup carries classes only (bx, hl, lane, h, mu, tg, lb, ln, dash, ah); colours
come from DIAGRAM_CSS, which reads the page's CSS custom properties, so the diagrams
follow the report's light and dark themes. Generate each option's schematic from one
spec per option, so they stay consistent with each other and with the metadata.
"""
from html import escape as esc

DIAGRAM_CSS = """
.dg{width:100%;height:auto;font:13px/1.3 var(--font, system-ui, sans-serif)}
.dg .bx{fill:var(--surface,#fff);stroke:var(--border,#8a8f98);stroke-width:1.2}
.dg .bx.hl{stroke:var(--accent,#2563eb);stroke-width:2}
.dg .bx.dash,.dg .ln.dash{stroke-dasharray:5 4}
.dg .lane{fill:var(--surface-2,#f3f4f6);stroke:none}
.dg .h{fill:var(--text,#111);font-weight:600}
.dg .mu{fill:var(--muted,#555)}
.dg .tg,.dg .lb{fill:var(--muted,#555);font-size:11px}
.dg .ln{fill:none;stroke:var(--muted,#555);stroke-width:1.4}
.dg .ah{fill:var(--muted,#555)}
figure.diagram .scroll{overflow-x:auto}
"""

DEFS = ('<defs><marker id="{id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path class="ah" d="M0 0L10 5L0 10z"/>'
        '</marker></defs>')


class Svg:
    def __init__(self, key, w, h, label):
        """key must be unique on the page (it names the arrow marker); label is the
        accessible description of the whole diagram."""
        self.key, self.w, self.h, self.aria = key, w, h, label
        self.parts = [DEFS.format(id=f"ah-{key}")]

    def add(self, s):
        self.parts.append(s)

    def rect(self, x, y, w, h, cls="bx", r=6):
        self.add(f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>')

    def text(self, x, y, s, cls="", anchor="start"):
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        c = f' class="{cls}"' if cls else ""
        self.add(f'<text x="{x}" y="{y}"{a}{c}>{esc(str(s))}</text>')

    def box(self, x, y, w, h, title, lines=(), cls="bx", center=False, tag=None):
        """A box with a bold title and muted lines; `tag` is a small right-aligned label."""
        self.rect(x, y, w, h, cls)
        tx, anchor = (x + w / 2, "middle") if center else (x + 14, "start")
        n = 1 + len(lines)
        ty = y + h / 2 - (n - 1) * 8 + 4 if center else y + 22
        self.text(tx, ty, title, "h", anchor)
        for i, line in enumerate(lines):
            self.text(tx, ty + 17 * (i + 1), line, "mu", anchor)
        if tag:
            self.text(x + w - 12, y + 22, tag, "tg", "end")

    def lane(self, x, y, w, h, name, when=None):
        """A horizontal swim lane for one actor, with its name and timing on the left."""
        self.rect(x, y, w, h, "lane", 4)
        self.text(x + 14, y + 26, name, "h")
        if when:
            self.text(x + 14, y + 44, when, "mu")

    def path(self, d, cls="ln", arrow=True):
        m = f' marker-end="url(#ah-{self.key})"' if arrow else ""
        self.add(f'<path class="{cls}" d="{d}"{m}/>')

    def label(self, x, y, s, anchor="middle", cls="lb"):
        self.text(x, y, s, cls, anchor)

    def svg(self):
        return (f'<svg class="dg" viewBox="0 0 {self.w} {self.h}" role="img" '
                f'aria-label="{esc(self.aria)}">' + "".join(self.parts) + "</svg>")


def figure(svg, caption):
    return (f'<figure class="diagram"><div class="scroll">{svg.svg()}</div>'
            f'<figcaption>{esc(caption)}</figcaption></figure>')


if __name__ == "__main__":
    s = Svg("demo", 800, 140, "Demo workflow")
    s.lane(10, 20, 780, 100, "Enumerator", "day 0")
    s.box(200, 45, 160, 52, "List household", ["roster of every child"], center=True)
    s.box(480, 45, 160, 52, "Register case", ["linked tracked entity"], cls="bx hl", center=True)
    s.path("M360 71 H480")
    print(f"<style>{DIAGRAM_CSS}</style>{figure(s, 'Demo')}")
