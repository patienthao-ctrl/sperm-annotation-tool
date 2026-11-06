import { ref, watch } from 'vue'
import { fadeThemeColors } from '../utils/motion'
function initial(): 'light' | 'dark' { try { return localStorage.getItem('annotation-theme') === 'dark' ? 'dark' : 'light' } catch { return 'light' } }
const theme = ref(initial())
watch(theme, value => {
  document.documentElement.dataset.theme = value
  try { localStorage.setItem('annotation-theme', value) } catch (error) { console.warn('[ui.preference_save_failed]', error) }
}, { immediate: true })
export const useAppearance = () => ({ theme, toggleTheme: () => { fadeThemeColors(); theme.value = theme.value === 'light' ? 'dark' : 'light' } })
