---
name: ppt-structure
title: PPT 结构化
summary: 把上传的 PPT 或图片整理成 Status / Next Steps 与 Risk / Mitigation 表格。
description: Parse uploaded PPTX or images into Status/Next Steps and Risk/Mitigation tables. Use when the user uploads a file and asks to structure, extract tables, or analyze slides. Not for generating Project Slide page 1.
triggers: 结构化, PPT结构化, 解析PPT, 提取PPT表格, Status, Mitigation, parse_pptx_structure
tools: parse_pptx_structure
---

# PPT structure

- Require a selected Agent session and an uploaded document listed in its current context. Use its `document_id`; never accept a local path or read another session's files.

- When the message contains `[document_id: <id>]`, or the current context lists one suitable uploaded document, and asks to structure / extract tables / analyze:
  - Call `parse_pptx_structure(document_id="<id>")`.
- Supports PPTX and uploaded PNG/JPG/JPEG files. Images use the text extracted during document upload.
- After output, ask in Chinese whether the structure is satisfactory.
- On revision feedback, revise the table from the already extracted source text. Re-run extraction only when the source evidence is no longer available in the current turn.
- Never invent structured tables without calling the tool.
