export const clamp = n => Math.max(0, Math.min(1, n));

export function normalizedRect(rect, page) {
  if (page.width <= 0 || page.height <= 0) return null;
  const r = [clamp((rect.left-page.left)/page.width), clamp((rect.top-page.top)/page.height),
    clamp((rect.right-page.left)/page.width), clamp((rect.bottom-page.top)/page.height)];
  return r[2] > r[0] && r[3] > r[1] ? r.map(n=>Math.round(n*1e6)/1e6) : null;
}

export function dragRect(start, end, page) {
  return normalizedRect({left:Math.min(start.x,end.x),top:Math.min(start.y,end.y),
    right:Math.max(start.x,end.x),bottom:Math.max(start.y,end.y)}, page);
}

export function boxStyle(r) {
  return {left:r[0]*100+'%',top:r[1]*100+'%',width:(r[2]-r[0])*100+'%',height:(r[3]-r[1])*100+'%'};
}
