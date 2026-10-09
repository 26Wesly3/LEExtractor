# LEExtractor v0.9.10 验收记录

本版修复论文详情可见但无法点击的问题。此前 `.paper-drawer` 的 `z-index:200!important` 覆盖了组件动态层级，而灰色遮罩仍为 1003，遮罩盖住详情。移除固定层级后，组件将详情保持在遮罩上方。

## 回归检查

- 新增测试在修改前于 1280 / 390 像素宽度均失败：详情层级 200 小于遮罩 1003。修改后，两种宽度均通过。
- DOM 测试加载实际 `web/src/styles.css`，使用真实 Vuetify 抽屉，检查计算后的层级及非 inert 状态，并验证种子选择、外部论文链接、关闭按钮、点击遮罩返回结果与遮罩清理。
- 前端 `npm run test`：12 项通过；构建与 Python 包内静态资源同步完成。
- Python 3.12 定向发布与启动检查：`test_release_version.py`、`test_release_self_contained.py`、`test_web_release.py`、`test_web_launcher.py`，34 项通过；`ruff check .` 通过。
- 当前 8000 服务的详情路由及 CSS 经真实 HTTP 检查，成功返回新资源；原项目 revision 11、100 篇文献及项目文件 SHA-256 在服务更新前后保持一致。

版本使用 v0.9.10 以区分旧包。检索、翻译与 Embedding 实现沿用 v0.9.9，其验收与来源限制见 [VALIDATION_v0.9.9.md](VALIDATION_v0.9.9.md)。现有项目数据不参与发布包打包，也不因更新静态资源而修改。

本次使用计算样式、真实组件 DOM 操作与 HTTP 静态文件检查；未进行浏览器视觉验收。
