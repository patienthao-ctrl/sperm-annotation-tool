// Order in which a reviewer sees the targets of a round (decision 0006).
// Tracks that may have jumped to another head come first and start with "are these the
// same sperm?"; then targets someone added by hand, targets that enter or leave mid-video
// and tracks held together mostly by weak boxes; then all the others. One weak target is
// shown a second time, unmarked, to measure how consistent the answers are.

export type TargetFlagKind = 'cross' | 'jump' | 'partial' | 'weak'
export interface TargetFlag { kind: TargetFlagKind; frame?: number; last?: number; with?: number }
export interface QueueEntry { objectId: number; ask: 'same' | 'what'; repeat: boolean; suspectFrame?: number }

const rank = (flags: TargetFlag[], manual: boolean) =>
  flags.some(f => f.kind === 'cross' || f.kind === 'jump') ? 0 : manual ? 1 : flags.some(f => f.kind === 'partial') ? 2 : flags.some(f => f.kind === 'weak') ? 3 : 4

export function buildQueue(targets: number[], flags: Record<number, TargetFlag[]>, manual: Set<number>): QueueEntry[] {
  const ordered = [...targets].sort((a, b) => rank(flags[a] ?? [], manual.has(a)) - rank(flags[b] ?? [], manual.has(b)) || a - b)
  const queue: QueueEntry[] = ordered.map(objectId => {
    const suspect = (flags[objectId] ?? []).find(f => f.kind === 'cross' || f.kind === 'jump')
    return { objectId, ask: suspect ? 'same' : 'what', repeat: false, ...(suspect?.frame != null ? { suspectFrame: suspect.frame } : {}) }
  })
  const flagged = queue.filter(e => rank(flags[e.objectId] ?? [], manual.has(e.objectId)) < 4).length
  const repeatOf = ordered.find(id => (flags[id] ?? []).some(f => f.kind === 'weak')) ?? ordered.find(id => rank(flags[id] ?? [], manual.has(id)) < 4)
  if (repeatOf != null && queue.length >= 8) queue.splice(Math.min(queue.length, flagged + 6), 0, { objectId: repeatOf, ask: 'what', repeat: true })
  return queue
}

/** Next unanswered card after ``index``, wrapping; -1 when everything is answered. */
export function nextOpen(queue: QueueEntry[], answered: (entry: QueueEntry) => boolean, index: number): number {
  for (let step = 1; step <= queue.length; step++) {
    const i = (index + step + queue.length) % queue.length
    if (!answered(queue[i])) return i
  }
  return -1
}
