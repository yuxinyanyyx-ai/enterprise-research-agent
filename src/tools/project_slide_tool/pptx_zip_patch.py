from __future__ import annotations

import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

# Extension schema URIs reused across many shapes on a slide — do not remap on clone.
_SHARED_EXT_URIS = frozenset(
    {
        "FF2B5EF4-FFF2-40B4-BE49-F238E27FC236",
        "9D8B030D-6E8A-4147-A177-3AD203B41FA5",
        "0D108BD9-81ED-4DB2-BD59-A6C34878D82A",
        "53640926-AAD7-44D8-BBD7-CCE9431645EC",
    }
)

_SHAPE_BLOCK_RE = re.compile(
    r"(<p:(?:sp|pic|cxnSp)\b.*?</p:(?:sp|pic|cxnSp)>|<p:graphicFrame\b.*?</p:graphicFrame>)",
    re.DOTALL,
)


@dataclass
class ShapePatch:
    shape_id: str
    left: int | None = None
    width: int | None = None
    hidden: bool = False
    text_lines: list[str] | None = None
    font_size_pt: int | None = None


@dataclass
class PatchCollector:
    """Collect slide edits; apply via zip XML patch (never lxml re-serialize)."""

    shapes: dict[str, ShapePatch] = field(default_factory=dict)
    text_pairs: list[tuple[str, str]] = field(default_factory=list)
    table_cells: list[tuple[str, int, int, str]] = field(default_factory=list)

    def _patch(self, shape_id: str | None) -> ShapePatch | None:
        if not shape_id:
            return None
        return self.shapes.setdefault(shape_id, ShapePatch(shape_id=shape_id))

    def set_text(self, shape, text: str, font_size_pt: int | None = None) -> None:
        from .pptx_utils import normalize_text, shape_cnv_id

        sid = shape_cnv_id(shape)
        patch = self._patch(sid)
        if patch is None:
            return
        patch.text_lines = normalize_text(text).split("\n") if text else [""]
        if font_size_pt is not None:
            patch.font_size_pt = int(font_size_pt)
        if normalize_text(text):
            patch.hidden = False

    def set_left(self, shape, left: int) -> None:
        from .pptx_utils import shape_cnv_id

        patch = self._patch(shape_cnv_id(shape))
        if patch is not None:
            patch.left = int(left)

    def set_width(self, shape, width: int) -> None:
        from .pptx_utils import shape_cnv_id

        patch = self._patch(shape_cnv_id(shape))
        if patch is not None:
            patch.width = int(width)

    def hide(self, shape) -> None:
        from .pptx_utils import shape_cnv_id

        patch = self._patch(shape_cnv_id(shape))
        if patch is not None:
            patch.hidden = True

    def show(self, shape) -> None:
        from .pptx_utils import shape_cnv_id

        patch = self._patch(shape_cnv_id(shape))
        if patch is not None:
            patch.hidden = False

    def replace_text(self, old: str, new: str) -> None:
        if old and old != new:
            self.text_pairs.append((old, new))

    def set_table_cell(self, table_shape, row: int, col: int, text: str) -> None:
        from .pptx_utils import normalize_text, shape_cnv_id

        sid = shape_cnv_id(table_shape)
        if sid:
            self.table_cells.append((sid, row, col, normalize_text(text)))


def slide_entry(slide_index: int) -> str:
    return f"ppt/slides/slide{slide_index + 1}.xml"


def read_slide_xml(pptx_path: Path, slide_index: int) -> str:
    with zipfile.ZipFile(pptx_path) as z:
        return z.read(slide_entry(slide_index)).decode("utf-8")


def rewrite_zip_entry(pptx_path: Path, entry: str, data: bytes) -> None:
    tmp = pptx_path.with_suffix(".tmp.pptx")
    with zipfile.ZipFile(pptx_path, "r") as zin, zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            blob = data if item.filename == entry else zin.read(item.filename)
            zi = zipfile.ZipInfo(filename=item.filename, date_time=item.date_time)
            zi.compress_type = item.compress_type
            zi.external_attr = item.external_attr
            zi.create_system = item.create_system
            zi.create_version = item.create_version
            zi.extract_version = item.extract_version
            zout.writestr(zi, blob, compress_type=item.compress_type)
    tmp.replace(pptx_path)


def write_slide_xml(pptx_path: Path, slide_index: int, xml: str) -> None:
    rewrite_zip_entry(pptx_path, slide_entry(slide_index), xml.encode("utf-8"))


def replace_at_text(xml: str, old: str, new: str) -> str:
    if not old or old == new:
        return xml
    old_tag = f"<a:t>{escape(old)}</a:t>"
    new_tag = f"<a:t>{escape(new)}</a:t>"
    if old_tag in xml:
        return xml.replace(old_tag, new_tag)
    raw_old = f"<a:t>{old}</a:t>"
    raw_new = f"<a:t>{new}</a:t>"
    if raw_old in xml:
        return xml.replace(raw_old, raw_new)
    return xml


def _patch_block_off_x(block: str, left: int) -> str:
    return re.sub(
        r'(<a:xfrm[^>]*>.*?<a:off x=")(\d+)(" y=")(\d+)(")',
        rf"\g<1>{left}\g<3>\g<4>\g<5>",
        block,
        count=1,
        flags=re.DOTALL,
    )


def _patch_block_ext_cx(block: str, width: int) -> str:
    return re.sub(
        r'(<a:xfrm[^>]*>.*?<a:ext cx=")(\d+)(" cy=")(\d+)(")',
        rf"\g<1>{width}\g<3>\g<4>\g<5>",
        block,
        count=1,
        flags=re.DOTALL,
    )


def _patch_block_hidden(block: str) -> str:
    if re.search(r'<p:cNvPr\b[^>]*\bhidden="1"', block):
        return block
    return re.sub(r"(<p:cNvPr\b[^>]*)(/?>)", r'\1 hidden="1"\2', block, count=1)


def _ooxml_sz(font_size_pt: int) -> str:
    return str(int(font_size_pt) * 100)


def _patch_block_font_size(block: str, font_size_pt: int) -> str:
    """Normalize all run/endPara font sizes in a shape block (OOXML sz = pt * 100)."""
    sz = _ooxml_sz(font_size_pt)
    return re.sub(r'\bsz="\d+"', f'sz="{sz}"', block)


def _patch_block_normalize_baseline(block: str) -> str:
    """Clear superscript/subscript so visual size matches normal runs."""
    return re.sub(r'\bbaseline="-?\d+"', 'baseline="0"', block)


def _patch_block_no_autofit(block: str) -> str:
    """Prevent PowerPoint from shrinking text to fit a narrow date box."""
    block = re.sub(r"<a:spAutoFit\s*/>", "<a:noAutofit/>", block)
    block = re.sub(r"<a:normAutofit\b[^>]*/>", "<a:noAutofit/>", block)
    block = re.sub(
        r"<a:normAutofit\b[^>]*>.*?</a:normAutofit>",
        "<a:noAutofit/>",
        block,
        flags=re.DOTALL,
    )
    return block


def _patch_block_text_lines(block: str, lines: list[str], font_size_pt: int | None = None) -> str:
    idx = 0
    sz = _ooxml_sz(font_size_pt) if font_size_pt is not None else "1100"

    def next_text() -> str:
        nonlocal idx
        text = lines[idx] if idx < len(lines) else ""
        idx += 1
        return escape(text)

    block = re.sub(r"<a:t\s*/>", lambda _m: f"<a:t>{next_text()}</a:t>", block)
    block = re.sub(r"<a:t>.*?</a:t>", lambda _m: f"<a:t>{next_text()}</a:t>", block, flags=re.DOTALL)
    if idx < len(lines):
        remaining = "\n".join(lines[idx:]).strip()
        if remaining:
            text = escape(remaining)
            run = f'<a:r><a:rPr lang="en-US" altLang="zh-CN" sz="{sz}" dirty="0"/><a:t>{text}</a:t></a:r>'
            if re.search(r"<a:endParaRPr\b", block):
                # Empty template cells: run must precede endParaRPr (OOXML order).
                block = re.sub(r"<a:endParaRPr\b", run + "<a:endParaRPr", block, count=1)
            else:
                block = re.sub(
                    r"(<a:p\b[^>]*>.*?)(</a:p>)",
                    rf"\1{run}\2",
                    block,
                    count=1,
                    flags=re.DOTALL,
                )
    if font_size_pt is not None:
        block = _patch_block_font_size(block, font_size_pt)
        block = _patch_block_no_autofit(block)
        block = _patch_block_normalize_baseline(block)
    return block


def _grow_table_block_rows(block: str, total_rows: int) -> str:
    rows = re.findall(r"<a:tr\b.*?</a:tr>", block, flags=re.DOTALL)
    if len(rows) >= total_rows or not rows:
        return block
    template = rows[1] if len(rows) > 1 else rows[-1]
    additions = template * (total_rows - len(rows))
    anchor = rows[-1]
    pos = block.rfind(anchor)
    if pos < 0:
        return block
    return block[: pos + len(anchor)] + additions + block[pos + len(anchor) :]


def _patch_table_block(block: str, cells: list[tuple[int, int, str]]) -> str:
    if not cells:
        return block
    max_row = max(row for row, _, _ in cells) + 1
    block = _grow_table_block_rows(block, max_row)
    rows = re.findall(r"<a:tr\b.*?</a:tr>", block, flags=re.DOTALL)
    if not rows:
        return block
    by_row: dict[int, dict[int, str]] = {}
    for row, col, text in cells:
        by_row.setdefault(row, {})[col] = text
    new_rows: list[str] = []
    for row_idx, row_xml in enumerate(rows):
        if row_idx not in by_row:
            new_rows.append(row_xml)
            continue
        cols = re.findall(r"<a:tc\b.*?</a:tc>", row_xml, flags=re.DOTALL)
        if not cols:
            new_rows.append(row_xml)
            continue
        col_map = by_row[row_idx]
        rebuilt_cols: list[str] = []
        for col_idx, tc in enumerate(cols):
            if col_idx in col_map:
                rebuilt_cols.append(_patch_block_text_lines(tc, [col_map[col_idx]]))
            else:
                rebuilt_cols.append(tc)
        pos = 0
        new_row = row_xml
        for col_idx, tc in enumerate(cols):
            if col_idx in col_map:
                new_row = new_row.replace(tc, rebuilt_cols[col_idx], 1)
        new_rows.append(new_row)
    out = block
    for old_row, new_row in zip(rows, new_rows):
        if old_row != new_row:
            out = out.replace(old_row, new_row, 1)
    return out


def _block_cnv_id(block: str) -> str | None:
    m = re.search(r'<p:cNvPr\b[^>]*\bid="(\d+)"', block)
    return m.group(1) if m else None


def _apply_shape_patch(block: str, patch: ShapePatch) -> str:
    out = block
    if patch.text_lines is not None:
        out = _patch_block_text_lines(out, patch.text_lines, patch.font_size_pt)
    if patch.left is not None:
        out = _patch_block_off_x(out, patch.left)
    if patch.width is not None:
        out = _patch_block_ext_cx(out, patch.width)
    if patch.hidden:
        out = _patch_block_hidden(out)
    return out


def apply_patches_to_slide_xml(xml: str, collector: PatchCollector) -> str:
    table_by_id: dict[str, list[tuple[int, int, str]]] = {}
    for sid, row, col, text in collector.table_cells:
        table_by_id.setdefault(sid, []).append((row, col, text))

    parts: list[str] = []
    last = 0
    for match in _SHAPE_BLOCK_RE.finditer(xml):
        parts.append(xml[last : match.start()])
        block = match.group(1)
        sid = _block_cnv_id(block)
        if sid and sid in collector.shapes:
            block = _apply_shape_patch(block, collector.shapes[sid])
        if sid and sid in table_by_id:
            block = _patch_table_block(block, table_by_id[sid])
        parts.append(block)
        last = match.end()
    parts.append(xml[last:])
    out = "".join(parts)
    for old, new in collector.text_pairs:
        out = replace_at_text(out, old, new)
    return out


def max_cnv_id(xml: str) -> int:
    ids = [int(x) for x in re.findall(r'<p:cNvPr id="(\d+)"', xml)]
    return max(ids) if ids else 0


def _remap_block_instance_guids(block: str) -> str:
    """Give cloned shape blocks fresh instance GUIDs (creationId, per-shape ext uris)."""
    out = block
    for guid in set(re.findall(r"\{([0-9A-Fa-f-]{36})\}", block)):
        if guid.upper() in _SHARED_EXT_URIS:
            continue
        new_guid = str(uuid.uuid4()).upper()
        out = out.replace(f"{{{guid}}}", f"{{{new_guid}}}")
    return out


def _strip_clone_cust_data(block: str) -> str:
    """Drop OTL tag relationships from a clone — sharing r:id breaks PowerPoint."""
    block = re.sub(r"<p:custDataLst\b.*?</p:custDataLst>", "", block, count=1, flags=re.DOTALL)
    block = re.sub(r"<p:nvPr>\s*</p:nvPr>", "<p:nvPr/>", block, count=1)
    return block


def clone_shape_block_in_xml(xml: str, source_id: str) -> tuple[str, str | None]:
    """Clone a shape block with fresh cNvPr id (and OTL guid when present)."""
    source_block = None
    old_guid = None
    for match in _SHAPE_BLOCK_RE.finditer(xml):
        block = match.group(1)
        if _block_cnv_id(block) != source_id:
            continue
        source_block = block
        m = re.search(r"OTLSHAPE_[MT]_([0-9a-f]{32})", block, flags=re.I)
        old_guid = m.group(1) if m else None
        break
    if not source_block:
        return xml, None
    new_id = str(max_cnv_id(xml) + 1)
    new_block = source_block
    if old_guid:
        new_block = new_block.replace(old_guid, uuid.uuid4().hex)
    new_block = _remap_block_instance_guids(new_block)
    new_block = _strip_clone_cust_data(new_block)
    new_block = re.sub(rf'\bid="{re.escape(source_id)}"', f'id="{new_id}"', new_block, count=1)
    insert_at = xml.rfind("</p:spTree>")
    if insert_at < 0:
        return xml, None
    return xml[:insert_at] + new_block + xml[insert_at:], new_id


def _patch_shape_block_off_x_by_id(xml: str, shape_id: str, left: int) -> str:
    parts: list[str] = []
    last = 0
    for match in _SHAPE_BLOCK_RE.finditer(xml):
        parts.append(xml[last : match.start()])
        block = match.group(1)
        if _block_cnv_id(block) == shape_id:
            block = _patch_block_off_x(block, left)
        parts.append(block)
        last = match.end()
    parts.append(xml[last:])
    return "".join(parts)


def expand_track_clusters_in_xml(xml: str, source_cluster: dict, extra: int) -> str:
    """Add milestone slots by cloning date + label shapes from an existing cluster."""
    from .pptx_utils import shape_cnv_id

    if extra <= 0:
        return xml
    date = source_cluster.get("date")
    label = source_cluster.get("label") or source_cluster.get("title")
    date_id = shape_cnv_id(date) if date else None
    label_id = shape_cnv_id(label) if label else None
    if not date_id or not label_id:
        return xml
    date_left = int(date.left)
    label_left = int(label.left)
    label_delta = label_left - date_left
    scratch_base = 11_000_000
    for i in range(extra):
        xml, new_date = clone_shape_block_in_xml(xml, date_id)
        if not new_date:
            break
        xml, new_label = clone_shape_block_in_xml(xml, label_id)
        if not new_label:
            break
        scratch = scratch_base + i * 500_000
        xml = _patch_shape_block_off_x_by_id(xml, new_date, scratch)
        xml = _patch_shape_block_off_x_by_id(xml, new_label, scratch + label_delta)
    return xml


def clone_otl_marker_block_in_xml(xml: str, source_id: str) -> str:
    """Insert a cloned OTL marker block with fresh ids (for CTA triangle in template)."""
    xml, new_id = clone_shape_block_in_xml(xml, source_id)
    return xml if new_id else xml


def copy_pptx(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
