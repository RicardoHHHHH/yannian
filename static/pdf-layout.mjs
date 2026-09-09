export const PAGE_GAP = 28;
export const PAGE_PADDING = 16;

export const TILE_PIXELS = 2048;

// Keep the same sampling quality at every zoom; limit allocations by visible tiles.
export function rasterTiles(width, height, deviceRatio=1, view={left:0,top:0,right:width,bottom:height}) {
  const scale=Math.max(4,Number.isFinite(deviceRatio)&&deviceRatio>0?deviceRatio:1);
  const fullWidth=Math.ceil(width*scale),fullHeight=Math.ceil(height*scale);
  const left=Math.max(0,view.left),top=Math.max(0,view.top),right=Math.min(width,view.right),bottom=Math.min(height,view.bottom);
  if(right<=left||bottom<=top)return [];
  const tiles=[];
  for(let y=Math.floor(top*scale/TILE_PIXELS)*TILE_PIXELS;y<Math.ceil(bottom*scale);y+=TILE_PIXELS){
    for(let x=Math.floor(left*scale/TILE_PIXELS)*TILE_PIXELS;x<Math.ceil(right*scale);x+=TILE_PIXELS){
      tiles.push({key:x+':'+y,x,y,width:Math.min(TILE_PIXELS,fullWidth-x),height:Math.min(TILE_PIXELS,fullHeight-y),scale});
    }
  }
  return tiles;
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
