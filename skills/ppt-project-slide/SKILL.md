---
name: ppt-project-slide
title: Project Slide
summary: 根据上传的 PPT 生成或修改 Project Slide 第 1 页，并给出下载链接。
description: Generate or modify Project Slide page 1 from an uploaded pptx. Use when the user wants a downloadable first page, timeline edits, or a new download link. Not for extracting Status/Risk tables.
triggers: Project Slide, 生成第1页, 修改slide, job_id, timeline, generate_project_slide, modify_project_slide
tools: generate_project_slide, modify_project_slide
---

# Project Slide

- This Skill operates only on PPTX files uploaded to the current Agent session. Use the opaque `file_id` returned by the session upload endpoint; never accept a local path from the user or use another session's file.
- If no session PPTX `file_id` is present, ask the user to upload a `.pptx` file before generating a slide.

## generate_project_slide

- When the user wants a downloadable Project Slide page 1 from an uploaded pptx:
  - Call `generate_project_slide(file_id="<id>")`.
  - Echo the tool's Markdown download link exactly and retain its 32-hex job ID for follow-up edits.

## modify_project_slide

- When editing a previously generated slide:
  - Use the latest Project Slide job ID from the current session context or the previous generated download link. Do not invent an ID or use one from another session.
  - Call `modify_project_slide(job_id="<job_id>", feedback="<user text>")`.
- Echo the new download link exactly.
