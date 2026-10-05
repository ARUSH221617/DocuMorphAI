---
name: page-layout-architect
description: Strictly forbids wrapping exam pages or questions inside tables. Reconstructs exams using clean, sequential Markdown headings, paragraphs, and lists while preserving headers, questions, points, and visuals.
---

# Page Layout Architect Skill

## CRITICAL RULE: NEVER USE TABLES FOR PAGE OR EXAM LAYOUT

Physical exam sheets often print questions inside borders or 3-column grids (`ردیف | شرح سوال | بارم`).
**DO NOT RECREATE THIS AS A TABLE IN MARKDOWN.**
Wrapping questions, multi-line text, formulas, or diagrams in Markdown tables breaks DOCX conversion, corrupts equation parsing, forces illegal `<br>` tags, and ruins Word pagination.

Tables in Markdown are **STRICTLY RESERVED** for actual tabular data (e.g., truth tables, function $x/y$ value tables, statistical data).

---

## 1. Exam Header Reconstruction

Do not put headers in complex grid tables. Use clean, bold text lines separated by pipes `|` or standard paragraphs:

```markdown
**بسمه تعالی**
**آزمون نوبت اول درس:** ریاضی پایه هشتم | **تاریخ آزمون:** ۱۴۰۲/۱۰/۱۵ | **مدت زمان:** ۸۰ دقیقه
**نام و نام خانوادگی:** ........................ | **شعبه / کلاس:** ............ | **شماره دانش‌آموز:** ........
**نام دبیر:** .................... | **نمره به عدد:** ............ | **نمره به حروف:** ............

---
```

---

## 2. Exam Question Reconstruction

Reconstruct questions as clean, standard Markdown with bold question numbers and point values:

```markdown
### سوالات آزمون

**۱-** جملات درست را با «ص» و نادرست را با «غ» مشخص کنید: (۱.۵ نمره)
- الف) قرینه هر عدد منفی، عددی مثبت است. ☐ ص ☐ غ
- ب) زاویه‌های متقابل به رأس با یکدیگر برابرند. ☐ ص ☐ غ
- ج) هر عدد طبیعی دارای حداقل دو شمارنده است. ☐ ص ☐ غ

**۲-** جاهای خالی را با کلمات یا اعداد مناسب کامل کنید: (۱ نمره)
- الف) تنها عدد اول زوج، عدد ............ است.
- ب) بردار قرینه بردار $\begin{bmatrix} -3 \\ 5 \end{bmatrix}$ برابر ............ است.
```

---

## 3. Multiple Choice Questions

Place options as an inline series or a clean list. **DO NOT USE A TABLE FOR CHOICES.**

```markdown
**۳-** کدام‌یک از اعداد زیر اول است؟ (۰.۵ نمره)
الف) $۲۱$
ب) $۲۷$
ج) $۲۳$
د) $۱۵$
```

Or inline:
```markdown
**۳-** کدام‌یک از اعداد زیر اول است؟ (۰.۵ نمره)
الف) $۲۱$     ب) $۲۷$     ج) $۲۳$     د) $۱۵$
```

---

## 4. Visuals and Diagrams (SVGs)

Place SVG diagrams directly beneath the relevant question. **NEVER EMBED SVGs INSIDE TABLE CELLS.**

```markdown
**۱۲-** در شکل زیر، بردار $\vec{a}$ و بردار $\vec{b}$ را در دستگاه مختصات رسم کرده‌ایم. مختصات هر بردار را بنویسید: (۱.۵ نمره)

```xml
<svg id="fig-q12" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 280" width="280" height="280">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-reverse">
      <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="#000" />
    </marker>
  </defs>
  <line x1="20" y1="140" x2="260" y2="140" stroke="#000" stroke-width="2" marker-end="url(#arrow)" />
  <line x1="140" y1="260" x2="140" y2="20" stroke="#000" stroke-width="2" marker-end="url(#arrow)" />
  <text x="250" y="130" font-family="Arial" font-size="14" font-weight="bold">x</text>
  <text x="150" y="30" font-family="Arial" font-size="14" font-weight="bold">y</text>
</svg>
```

الف) مختصات بردار $\vec{a} = \begin{bmatrix} 3 \\ 2 \end{bmatrix}$
ب) مختصات بردار $\vec{b} = \begin{bmatrix} -2 \\ 3 \end{bmatrix}$
```

---

## 5. Summary Checklist

1. **NO whole-page layout tables**: Keep flow sequential.
2. **NO question-grid tables**: Use `**۱-** ...`, `**۲-** ...`.
3. **NO SVGs inside table cells**: SVGs are standalone blocks.
4. **NO `<br>` tags**: Use Markdown newlines.
5. **Tables only for data**: Tables are only for actual math/data tables (e.g. frequency distributions, coordinate lists).
