"""PDF text blocks retain stable page coordinates for evidence navigation."""
import hashlib
import re
from pathlib import Path
import pymupdf as fitz

MAX_BYTES = 40 * 1024 * 1024
MAX_PAGES = 500


def clean(text):
    text = re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def ordered_blocks(page):
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and clean(b[4])]
    width = page.rect.width
    # Recognize a conventional two-column page. Wide headings split reading bands.
    left = [b for b in blocks if b[0] < width * .4 and b[2] < width * .6]
    right = [b for b in blocks if b[0] >= width * .4]
    if len(left) < 2 or len(right) < 2:
        return sorted(blocks, key=lambda b: (round(b[1] / 5), b[0]))
    spanning = sorted([b for b in blocks if b[0] < width * .4 and b[2] >= width * .6], key=lambda b: b[1])
    result, remaining = [], list(blocks)
    for head in spanning + [None]:
        cutoff = head[1] if head else float("inf")
        band = [b for b in remaining if b is not head and b[1] < cutoff]
        result.extend(sorted(band, key=lambda b: (0 if b[0] < width * .4 else 1, b[1])))
        for b in band:
            remaining.remove(b)
        if head in remaining:
            result.append(head)
            remaining.remove(head)
    return result


def guess_title(page, filename):
    lines = []
    for block in page.get_text('dict').get('blocks', []):
        for line in block.get('lines', []):
            spans = line.get('spans', [])
            # Adjacent small-cap spans are parts of a word, not separate words.
            text = clean(''.join(span['text'] for span in spans))
            if spans and 4 < len(text) < 350 and line['bbox'][1] < page.rect.height * .5 and abs(line.get('dir', (1, 0))[1]) < .2:
                lines.append({'text':text, 'size':max(span['size'] for span in spans), 'box':line['bbox']})
    if not lines:
        return Path(filename).stem
    lines.sort(key=lambda line:(line['box'][1],line['box'][0]))
    anchor = max(range(len(lines)), key=lambda i:lines[i]['size'])
    size = lines[anchor]['size']
    start = end = anchor
    def adjacent(above, below):
        return (min(above['size'], below['size']) >= size * .9
                and below['box'][1] - above['box'][3] < size * 1.5
                and abs((above['box'][0]+above['box'][2])-(below['box'][0]+below['box'][2])) < page.rect.width * .7)
    while start > 0 and adjacent(lines[start-1], lines[start]):
        start -= 1
    while end+1 < len(lines) and adjacent(lines[end], lines[end+1]):
        end += 1
    title = clean(' '.join(line['text'] for line in lines[start:end+1]))
    return title[:1000] or Path(filename).stem


def parse_pdf(data, filename):
    if len(data) > MAX_BYTES:
        raise ValueError("PDF 超过 40 MB，请拆分后导入。")
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise ValueError("文件不是有效的 PDF。")
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError("无法读取 PDF，文件可能损坏。") from exc
    with doc:
        if doc.needs_pass:
            raise ValueError("请先解除 PDF 的密码保护。")
        if not 0 < len(doc) <= MAX_PAGES:
            raise ValueError("支持 1–500 页 PDF，请拆分后导入。")
        paragraphs = []
        for page_number, page in enumerate(doc, 1):
            for order, b in enumerate(ordered_blocks(page)):
                text = clean(b[4])
                if not text:
                    continue
                # Keep each original block intact: splitting fabricated paragraph boundaries
                # would make the highlight disagree with the source PDF.
                paragraphs.append({"page": page_number, "ordinal": order, "text": text,
                                   "bbox": [round(x, 2) for x in b[:4]], "kind": "text"})
        title = clean(doc.metadata.get("title") or "")
        if not title or title.lower().endswith((".docx", ".tex", ".pdf")):
            title = guess_title(doc[0], filename)
        return {"title": title, "authors": clean(doc.metadata.get("author") or ""),
                "page_count": len(doc), "paragraphs": paragraphs,
                "sha256": hashlib.sha256(data).hexdigest(),
                "abstract": " ".join(p["text"] for p in paragraphs[:8])[:3500],
                "warning": "未提取到文字。这可能是扫描件；仍可查看原文并使用页面图像提问。" if not paragraphs else ""}


def page_png(path, page_number, box=None, scale=1.6):
    with fitz.open(path) as doc:
        if not 1 <= page_number <= len(doc):
            raise ValueError("页码超出范围。")
        page = doc[page_number - 1]
        if box:
            page.add_highlight_annot(fitz.Rect(box))
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
