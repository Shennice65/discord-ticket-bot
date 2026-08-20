import re

with open('clips/static/title.svg', 'r') as f:
    svg_data = f.read()

paths = re.findall(r'<path[^>]*d="[^"]*"[^>]*/>', svg_data)
paths_html = '\n                    '.join(paths)

with open('clips/templates/index.html', 'r') as f:
    html = f.read()

new_svg = f"""<svg class="brand-svg" viewBox="-3.725 -2 716.425 94" preserveAspectRatio="xMinYMid meet">
                    <g class="brand-text-group" stroke-linecap="round" fill-rule="evenodd">
                        {paths_html}
                    </g>
                </svg>"""

html = re.sub(r'<svg class="brand-svg".*?</svg>', new_svg, html, flags=re.DOTALL)

with open('clips/templates/index.html', 'w') as f:
    f.write(html)
