---
name: markitdown
description: Convert PDF, PowerPoint, Word, Excel, HTML, CSV, images, audio, and other supported files to Markdown with the Microsoft MarkItDown CLI. Use when the user asks to convert a document to Markdown, extract document text, or mentions MarkItDown.
---

# MarkItDown

Convert documents to Markdown without reading binary inputs into the conversation context.

## Rules

1. Never use a text-reading tool on a binary input such as PDF, Office files, images, or media.
2. Locate an input by its explicit path; if the user provides only a partial name, search filenames before converting.
3. Default output is the input basename with a `.md` suffix in the same directory, unless the user specifies another output path.
4. Read only the generated Markdown for a short preview after a successful conversion.

## Workflow

1. Check that `markitdown` is available with `command -v markitdown`.
2. If absent, ask for approval before installing `markitdown[all]`; do not rely on the Claude Code session-start hook because Codex does not run it.
3. Convert one file with:

```bash
markitdown "<input-file>" > "<output-file>.md"
```

4. For a user-requested batch, process each matching file separately and stop to report any failed conversion.
5. Report the input path, output path, and a preview of the first 20 output lines.

## Failure handling

Report the command error unchanged in a concise summary. Common causes are unsupported formats, corrupted source files, missing optional MarkItDown extras, or an output directory without write permission.
