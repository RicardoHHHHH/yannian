export const PAGE_GAP = 28;
export const PAGE_PADDING = 16;

// Supersample small text, honor high-DPI screens, and bound each canvas allocation.
export function canvasSize(width, height, deviceRatio=1) {
  const ratio=Number.isFinite(deviceRatio)&&deviceRatio>0?deviceRatio:1;
  const scale=Math.min(Math.max(2,ratio),Math.sqrt(20_000_000/(width*height)),8192/width,8192/height);
  const pixelsWide=Math.max(1,Math.floor(width*scale)),pixelsHigh=Math.max(1,Math.floor(height*scale));
  return {width:pixelsWide,height:pixelsHigh,scaleX:pixelsWide/width,scaleY:pixelsHigh/height};
}

export function layoutPages(sizes, zoom, availableWidth) {
  let top = PAGE_PADDING;
  return sizes.map(([width, height], index) => {
    const scale = zoom === 'fit' ? Math.max(180, availableWidth) / width : Number(zoom) * 96 / 72;
    const page = {page: index + 1, top, width: width * scale, height: height * scale};
    page.bottom = page.top + page.height;
    top = page.bottom + PAGE_GAP;
    return page;
  });
}

export function nearbyPages(pages, scrollTop, viewportHeight) {
  const from = scrollTop - viewportHeight, to = scrollTop + viewportHeight * 2;
  return pages.filter(p => p.bottom >= from && p.top <= to).map(p => p.page);
}

export function readingPage(pages, scrollTop, viewportHeight) {
  const probe = scrollTop + Math.min(120, viewportHeight * .25);
  return (pages.find(p => p.bottom > probe) || pages.at(-1))?.page || 1;
}

// Keep the same place within the page when zooming or resizing the reader.
export function capturePosition(pages, scrollTop) {
  const page = pages.find(p => p.bottom > scrollTop) || pages.at(-1);
  return page ? {page: page.page, fraction: Math.max(0, Math.min(1, (scrollTop - page.top) / page.height))} : {page: 1, fraction: 0};
}

export function restorePosition(pages, position) {
  const page = pages[position.page - 1];
  return page ? Math.max(0, page.top + page.height * position.fraction) : 0;
}
