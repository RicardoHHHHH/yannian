"""Stable selections in normalized, displayed PDF page coordinates."""
import json
import math
import pymupdf as fitz
from fastapi import HTTPException
from . import db


def get(selection_id):
    item = db.one('SELECT * FROM selections WHERE id=?', (selection_id,))
    if not item: raise HTTPException(404, 'PDF 选区不存在。')
    item['rects'] = json.loads(item['rects'])
    item['image_url'] = '/api/selections/' + item['id'] + '/image.png'
    return item


def validate_rects(rects):
    if not 1 <= len(rects) <= 256: raise ValueError('选区数量需要在 1–256 之间。')
    for r in rects:
        if len(r) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in r) or r[2] <= r[0] or r[3] <= r[1]:
            raise ValueError('选区坐标无效，请在 PDF 页面内重新选择。')
    return rects


def display_rect(page, rect):
    w, h = page.rect.width, page.rect.height
    return fitz.Rect(rect[0]*w, rect[1]*h, rect[2]*w, rect[3]*h)


def create(paper, page_number, kind, text, rects):
    if not paper.get('pdf_path'): raise HTTPException(400, '请先获取 PDF。')
    try:
        validate_rects(rects)
        with fitz.open(db.DATA / paper['pdf_path']) as document:
            if not 1 <= page_number <= len(document): raise ValueError('页码超出范围。')
            page = document[page_number-1]
            unrotated = [display_rect(page, r) * page.derotation_matrix for r in rects]
            if kind == 'region':
                text = '\n'.join(page.get_text('text', clip=r, sort=True).strip() for r in unrotated)[:15000]
            elif not text.strip(): raise ValueError('没有选中文字；扫描件或图片请使用“框选图片/区域”。')
            paragraphs = db.rows('SELECT id,bbox FROM paragraphs WHERE paper_id=? AND page=?', (paper['id'], page_number))
            scored = [(sum((fitz.Rect(json.loads(p['bbox'])) & r).get_area() for r in unrotated), p['id']) for p in paragraphs]
            score, paragraph_id = max(scored, default=(0, None))
            if not score: paragraph_id = None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    identifier = db.uid()
    db.execute('INSERT INTO selections VALUES (?,?,?,?,?,?,?,?)',
               (identifier, paper['id'], paragraph_id, page_number, kind, text.strip(), json.dumps(rects), db.now()))
    return get(identifier)


def image(selection):
    paper = db.one('SELECT pdf_path FROM papers WHERE id=?', (selection['paper_id'],))
    if not paper or not paper['pdf_path']: raise HTTPException(404, '原 PDF 不存在。')
    with fitz.open(db.DATA / paper['pdf_path']) as document:
        page = document[selection['page']-1]
        rects = [display_rect(page, r) for r in selection['rects']]
        box = fitz.Rect(rects[0])
        for rect in rects[1:]: box |= rect
        scale = min(3, 2200 / max(box.width, box.height), math.sqrt(4_000_000 / max(1, box.get_area())))
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=box, alpha=False).tobytes('png')
