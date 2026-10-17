// 外壳与页面共享的两个偏好（UI_INTEGRATION_PLAN.md S2）。
//
// 它们原来各自定义在 App.vue 里：顶栏的语言开关、`.reduced` 的开关、以及每个
// 页面的 t() 都读同一份状态。外壳接管顶栏之后，状态必须只有一份，否则点语言
// 只换外壳、不换正文。
import { ref, watch } from 'vue'

const read = (key, fallback) => {
  try { return localStorage.getItem(key) ?? fallback } catch { return fallback }
}
const write = (key, value) => {
  try { localStorage.setItem(key, value) } catch { /* 隐私模式下写入失败不影响界面 */ }
}

/** 界面语言：'zh' | 'en'。 */
export const lang = ref(read('le.language', 'zh'))

/** 是否减弱动效（A12 的唯一开关，另外还遵守系统设置）。 */
export const reduced = ref(read('le.reduced', 'false') === 'true')

watch(lang, value => {
  write('le.language', value)
  if (typeof document !== 'undefined') document.documentElement.lang = value === 'zh' ? 'zh-CN' : 'en'
}, { immediate: true })

watch(reduced, value => {
  write('le.reduced', String(value))
  if (typeof document !== 'undefined' && document.body) document.body.classList.toggle('reduced', value)
}, { immediate: true })

/** 系统层面的减弱动效；与用户开关取并集。 */
export function motionReduced() {
  return reduced.value || (typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
}
