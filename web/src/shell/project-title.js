// 工作区当前项目名的唯一来源（S4）。
//
// 阶段条搬到外壳之后，外壳需要显示「当前项目」的名字，而名字只有 Workspace
// 通过 GET /api/v3/projects/{id} 拿得到。用一个最小的响应式单例把它传上去，
// 避免外壳重复请求同一份事实。
import { ref } from 'vue'

const title = ref('')

export const projectTitle = title

export function setProjectTitle(value) {
  title.value = typeof value === 'string' ? value : ''
}

export function clearProjectTitle() {
  title.value = ''
}
