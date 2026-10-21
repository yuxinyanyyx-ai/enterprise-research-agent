"""Native document text, with image markup removed before evidence is created."""
import re


def image_reference_labels(text: str) -> set[str]:
    labels = set()
    for match in re.finditer(r"!\[([^\]\n]*)\](?:\[([^\]\n]*)\])?(?!\()", text):
        labels.add((match.group(2) or match.group(1)).casefold())
    return labels


def clean_image_markup(text: str, *, image_refs: set[str] | None = None) -> str:
    # Image alt text is not document evidence. Retain ordinary links and prose.
    image_refs = set(image_refs or ())
    output, cursor = [], 0
    for match in re.finditer(r"!\[([^\]\n]*)\]", text):
        if match.start() < cursor:
            continue
        end = match.end()
        if text[end:end + 1] == "(":
            depth, i = 1, end + 1
            while i < len(text) and depth:
                if text[i] == "\\":
                    i += 2
                    continue
                depth += (text[i] == "(") - (text[i] == ")")
                i += 1
            if depth == 0:
                end = i
        elif text[end:end + 1] == "[":
            ref = re.match(r"\[([^\]\n]*)\]", text[end:])
            if ref:
                image_refs.add((ref.group(1) or match.group(1)).casefold())
                end += ref.end()
        else:
            image_refs.add(match.group(1).casefold())
        output.append(text[cursor:match.start()])
        cursor = end
    output.append(text[cursor:])
    text = "".join(output)
    text = re.sub(r"<img\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"<!--\s*(?:image|图片|图像)\b.*?-->", "", text, flags=re.I | re.S)
    lines = []
    for line in text.splitlines():
        if any(re.match(r"^\s*\[" + re.escape(ref) + r"\]:", line, re.I) for ref in image_refs):
            continue
        if re.fullmatch(r"\s*(?:[-*]\s+)?(?:\[(?:图片|图像|image)(?:\s*\d+)?(?:\s*[:：][^\]]*)?\]|【(?:图片|图像)(?:\s*\d+)?】|<image>|图片|图像)\s*", line, re.I):
            continue
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def evidence_markdown(evidence: list[dict]) -> str:
    """The same ordered evidence feeds the saved MD and the document topic model."""
    return "\n\n".join(
        f"## {c.get('loc', 'Document')}\n\nEvidence: {c['chunk_id']}\n\n{c['text']}"
        for c in evidence
    ) + ("\n" if evidence else "")
