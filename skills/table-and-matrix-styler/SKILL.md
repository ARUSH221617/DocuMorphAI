---
name: table-and-matrix-styler
description: Guidelines for structuring clean Markdown tables and matrices without line breaking or corrupted cell layout.
---

# Table and Matrix Styler Skill

## Core Principles
1. Markdown table syntax:
   - Format: `| Column 1 | Column 2 | Column 3 |`.
   - Header separator: `| :--- | :---: | ---: |`.
2. Rules:
   - Every row MUST be on exactly ONE line.
   - Never use `<br>` or multiline text inside a single cell; use comma or punctuation instead.
   - Escape pipes inside cell content using `\|`.
   - Math inside cells: `$x^2$` is fully supported.
