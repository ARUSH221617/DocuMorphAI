---
name: math-formula-extractor
description: Expert rules for converting complex mathematical expressions, matrices, piecewise functions, and fractions to Word OMML-compatible LaTeX.
---

# Math Formula Extractor Skill

## Core Principles
1. Always write inline math wrapped in `$ ... $` and display/block equations in `$$ ... $$`.
2. Word OMML compatibility:
   - Use standard `\frac{numerator}{denominator}`.
   - Use standard symbols: `\pm`, `\times`, `\div`, `\cdot`, `\le`, `\ge`, `\approx`, `\neq`.
   - Greek letters: `\alpha`, `\beta`, `\theta`, `\pi`, `\Delta`, `\lambda`, `\mu`, `\sigma`.
   - Vectors: use `\vec{a}` or `\mathbf{v}`.
   - Square roots: `\sqrt{x}` or `\sqrt[n]{x}`.
3. Gotchas to avoid:
   - Do NOT use unescaped ampersands `&` outside of `\begin{matrix}` or aligned environments.
   - Do NOT use non-standard LaTeX packages or commands (e.g. `\color`, `\tikz`).
   - Do NOT use `\qquad` or `\quad` in regular text between multiple-choice options (e.g. write standard spaces instead: `الف) ...    ب) ...`).
   - For spacing inside formulas, write `\text{    }` instead of raw `\qquad` so Word renders clean spacing.
   - Keep LaTeX pure without embedded HTML.
