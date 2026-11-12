// Check-frame scanning in blocks (decision 0006): the frame is cut into overlapping blocks,
// each shown large enough to see a head, visited in a snake order so every step moves to a
// neighbour. Coordinates are fractions of the frame (0–1).

export interface ScanBlock { x0: number; y0: number; x1: number; y1: number; row: number; col: number }

/** Screen pixels per source pixel wanted while scanning: a 4K head (~25 px) shows at ~15 px. */
export const scanScale = (mediaW: number, mediaH: number) => Math.min(2, 0.6 * 4096 / Math.max(mediaW, mediaH, 1))
const OVERLAP = 0.1

export function planBlocks(mediaW: number, mediaH: number, viewW: number, viewH: number): ScanBlock[] {
  if (!(mediaW > 0 && mediaH > 0 && viewW > 0 && viewH > 0)) return []
  const s = scanScale(mediaW, mediaH)
  // Source pixels one view holds at that scale, less the overlap with the next block.
  const cols = Math.max(1, Math.ceil(mediaW / (viewW / s * (1 - OVERLAP))))
  const rows = Math.max(1, Math.ceil(mediaH / (viewH / s * (1 - OVERLAP))))
  if (cols === 1 && rows === 1) return [{ x0: 0, y0: 0, x1: 1, y1: 1, row: 0, col: 0 }]
  const span = (n: number) => n === 1 ? 1 : Math.min(1, (1 / n) * (1 + OVERLAP))
  const w = span(cols), h = span(rows)
  const at = (i: number, n: number, size: number) => n === 1 ? 0 : i * (1 - size) / (n - 1)
  const out: ScanBlock[] = []
  for (let row = 0; row < rows; row++) {
    for (let k = 0; k < cols; k++) {
      const col = row % 2 ? cols - 1 - k : k
      const x0 = at(col, cols, w), y0 = at(row, rows, h)
      out.push({ x0, y0, x1: x0 + w, y1: y0 + h, row, col })
    }
  }
  return out
}

/** Dashed hints: model candidates no current box already covers (centre inside a box, padded). */
export function openHints(hints: number[][], boxes: number[][], pad = 4): number[][] {
  return hints.filter(h => {
    const cx = (h[0] + h[2]) / 2, cy = (h[1] + h[3]) / 2
    return !boxes.some(b => cx >= b[0] - pad && cx <= b[2] + pad && cy >= b[1] - pad && cy <= b[3] + pad)
  })
}
