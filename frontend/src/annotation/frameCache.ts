// Blob cache has both a byte budget and an entry budget; failed requests are never cached.
export class FrameCache {
  private entries = new Map<string, Blob>()
  private pending = new Map<string, Promise<Blob>>()
  private bytes = 0
  private generation = 0
  constructor(private maxEntries = 12, private maxBytes = 32 * 1024 * 1024) {}
  has(key: string) { return this.entries.has(key) }
  clear() { this.generation++; this.entries.clear(); this.pending.clear(); this.bytes = 0 }
  invalidate(key: string) {
    const cached = this.entries.get(key)
    if (cached) { this.bytes -= cached.size; this.entries.delete(key) }
    this.pending.delete(key)
  }
  async get(key: string, load: () => Promise<Blob>): Promise<Blob> {
    const cached = this.entries.get(key)
    if (cached) { this.entries.delete(key); this.entries.set(key, cached); return cached }
    const ongoing = this.pending.get(key)
    if (ongoing) return ongoing
    const generation = this.generation
    const request = load().then(blob => {
      if (generation === this.generation && this.pending.get(key) === request && blob.size <= this.maxBytes) {
        this.entries.set(key, blob); this.bytes += blob.size
        while (this.entries.size > this.maxEntries || this.bytes > this.maxBytes) {
          const first = this.entries.keys().next().value!
          this.bytes -= this.entries.get(first)!.size; this.entries.delete(first)
        }
      }
      return blob
    }).finally(() => { if (this.pending.get(key) === request) this.pending.delete(key) })
    this.pending.set(key, request)
    return request
  }
}
