---
name: svg-diagram-architect
description: Strict vector guidelines for geometry, coordinates, graphs, and shapes to enable Word 'Convert to Shape'.
---

# SVG Diagram Architect Skill

## Core Principles
1. Output SVG inside code fences: ```xml ... ``` or ```svg ... ```.
2. SVG requirements for Word conversion:
   - Root `<svg>` MUST include `xmlns="http://www.w3.org/2000/svg"`.
   - MUST include `viewBox="0 0 W H"` attribute.
   - MUST specify explicit `width` and `height` attributes (e.g. `width="280" height="280"`).
3. Visual styling:
   - Use standard geometric elements: `<line>`, `<circle>`, `<rect>`, `<polygon>`, `<polyline>`, `<path>`.
   - Gridlines: `<line stroke="#d1d5db" stroke-width="1" stroke-dasharray="3,3" />`.
   - Vectors: define `<marker id="arrow" ...>` in `<defs>` and use `marker-end="url(#arrow)"`.
   - Labels: use `<text font-family="Arial" font-size="14" font-weight="bold">` placed with clear clearance.
4. Clean self-contained tags: close every tag properly. No external image links or external fonts.
