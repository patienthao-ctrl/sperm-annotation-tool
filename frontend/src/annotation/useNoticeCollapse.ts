import { computed, ref, watch, type ComputedRef } from 'vue'
import { collapseNotice, retainActiveNotices } from './notices'

// `keys` maps each collapsible notice to a key naming its exact failure ('' when it is not active).
export function useNoticeCollapse<Name extends string>(keys: ComputedRef<Record<Name, string>>) {
  const collapsed = ref<string[]>([])
  const active = computed(() => (Object.values(keys.value) as string[]).filter(Boolean))
  watch(active, list => { collapsed.value = retainActiveNotices(collapsed.value, list) })
  return {
    noticeOpen: (name: Name) => !!keys.value[name] && !collapsed.value.includes(keys.value[name]),
    collapse: (name: Name) => { collapsed.value = collapseNotice(collapsed.value, keys.value[name]) },
    expandNotices: () => { collapsed.value = [] },
    collapsedCount: computed(() => retainActiveNotices(collapsed.value, active.value).length),
  }
}
