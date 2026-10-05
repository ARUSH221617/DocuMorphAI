---
name: persian-rtl-typesetter
description: Best practices for Persian and Arabic academic questions, RTL direction, Persian numeral layout, and bilingual math phrasing.
---

# Persian RTL Typesetter Skill

## Core Principles
1. Persian question headings:
   - Format questions cleanly: `**۱-** سوال...` or `### سوال ۱`.
   - Keep questions clearly separated with blank lines.
2. Formula handling:
   - Math formulas in Persian sentences remain standard LTR LaTeX `$x + y = 5$`.
   - Persian numbers inside regular text (e.g. `۱۲.۵`), but variable names in math remain standard English (`x`, `y`, `z`).
3. Layout:
   - Multiple choice options: use clean markdown list items or a single-row 4-column table:
     `| (الف) ... | (ب) ... | (ج) ... | (د) ... |`.
