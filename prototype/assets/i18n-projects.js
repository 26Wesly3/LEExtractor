/* ============================================================================
   LEExtractor — 项目列表与会话恢复页双语文案（页面级字典）
   ----------------------------------------------------------------------------
   加载顺序（必须严格如此，且在本文件之后才是页面脚本）：
     assets/data.js → assets/app.js → assets/i18n-pages.js
       → assets/i18n-projects.js → 页面内的 <script>

   用法：const s = (k, v) => LE.pt('projects', k, v);

   约定：页面文案一律走 LE.pt()，**不要**给页面文案加 data-i18n 属性 ——
   applyI18n() 只认 app.js 的共享字典，会把页面 key 原样写出来。

   业务约束（见 docs/known-gaps-v095.md G1）：会话文件不保存 stop_reason 与
   http_budget，恢复后 stop_reason 回落为空，因此**本页不显示停止原因**。
   ========================================================================== */

'use strict';

Object.assign(window.LE_PAGES, {
  projects: {
    zh: {
      skip: '跳到主内容',
      title: '项目列表',
      lead: '已保存会话的恢复、导入与冲突处理。**会话文件不保存停止原因与 HTTP 预算**，所以这里看不到上次检索是怎么结束的（见下方缺口 G1）。',
      toSearch: '新建检索 →',
      toResults: '当前项目结果 →',
      toSettings: '设置与诊断 →',

      /* ---------------------------------------------------------- 会话卡片 */
      gridTitle: '已保存会话',
      gridNote: '卡片上的数字来自各会话文件本身；「示例数据」表示该项目使用合成的演示语料，不是真实文献。',
      current: '当前项目',
      continueAction: '继续',
      restore: '恢复会话',
      saveAs: '另存为',
      restoreToast: '演示原型：恢复会话将在阶段二真正加载检查点、语料与筛选进度（{topic}）。',
      saveAsToast: '演示原型：另存为将在阶段二经 session_schema 校验后写出会话副本（{topic}）。',
      papersLabel: '文献',
      papersUnit: '{n} 篇',
      phaseLabel: '阶段',
      phaseScreening: '筛选与 PRISMA',
      phaseSearch: '系统检索',
      phaseExpand: '引文扩展',
      phaseUnknown: '未知',
      updatedLabel: '更新于',
      schemaLabel: '会话 schema',
      projectIdLabel: 'project_id',
      directionLabel: '研究方向',
      noDirection: '未填写研究方向',
      screeningLabel: '筛选状态',
      screenNotStarted: '尚未开始',
      screenPreliminary: '初筛已纳入（全文未完成）',
      screenFinal: '最终纳入',
      screenExcluded: '已结束且无纳入',
      stopHiddenNote: '卡片上不显示「停止原因」：会话文件不保存 stop_reason，恢复后回落为空 —— 原因见下方缺口 G1。停止原因只出现在当前运行态的视图里（任务卡、引文扩展页）。',

      /* ---------------------------------------------------------- 损坏会话 */
      corruptTitle: '损坏会话',
      corruptNote: '校验失败或无法读取的会话单独列在这里，不与可用项目混排。',
      corruptBadge: '损坏',
      corruptReasonLabel: '失败原因',
      corruptBackupLabel: '备份文件',
      corruptPolicy: '失败先备份、不修改原文件、不破坏自动保存；未来版本拒绝而非降级。',
      corruptNoContinue: '这一张卡没有「继续」按钮：损坏会话不会被静默修复，也不会降级读取。',

      /* ---------------------------------------------------------- 缺口 G1 */
      gapTitle: '已知缺口 G1：会话不保存停止原因与 HTTP 预算',
      gapBody: 'persistence.state_to_dict() 只写 17 个顶层键，**不包含 stop_reason、http_budget、run_history**；恢复会话后 stop_reason 回落为空。也就是说，从项目列表恢复一个会话时，界面无法知道上次检索是怎么结束的 —— 是饱和、超轮数，还是预算耗尽，全都丢了。这正是 v0.9.5 最强调的一件事：绝不把失败说成覆盖完成。所以项目卡片**不显示**停止原因：数据里虽然有这个字段，但它在恢复后是运行态事实，显示出来就是把丢失的信息假装成已知。',
      gapFix: '阶段二建议：state_to_dict 增加 stop_reason / http_budget 两个键并进 schema；或明确接受「运行态事实不入会话」，恢复后把这两个字段显示为「本次运行不可知」，而不是留空。',

      /* ---------------------------------------------------------- 导入 */
      importTitle: '导入会话',
      importNote: '导入必须经过 **session_schema 校验**：未来 schema 版本**拒绝**而不是降级读取，损坏文件先备份再处理。',
      importPick: '选择会话文件',
      importNone: '未选择文件',
      importChosen: '已选择：{name}',
      importToast: '演示原型：阶段二才对 {name} 做 session_schema 校验；本页不读取文件内容。',
      importStatic: '纯静态原型：不读取文件内容，也不写入任何文件。',
      importHint: '接受的扩展名：.json / .session / .leext（以 session_schema 判定为准）。',

      /* ---------------------------------------------------------- 冲突 */
      conflictTitle: '写入冲突',
      conflictBadge: '冲突检测有效',
      conflictBody: 'project_id 与写入冲突检测仍然有效：多个浏览器或进程同时写同一个 project_id 会被检测到，不会静默覆盖。',
      conflictNote: '冲突时的处理顺序：先备份现有文件，再让用户选择保留哪一份；绝不自动合并，也不静默覆盖。',

      /* ---------------------------------------------------------- 离线演示 */
      demoTitle: '离线演示项目',
      demoBody: '用明确标注的 SYNTHETIC DEMO 样本创建一个隔离项目：与已有会话完全分离，不写入任何真实项目。',
      demoBtn: '创建离线演示项目',
      demoToast: '演示原型：阶段二会用 SYNTHETIC DEMO 样本创建隔离项目并打开它。',

      empty: '没有已保存的会话。',
    },

    en: {
      skip: 'Skip to main content',
      title: 'Projects',
      lead: 'Restore, import and de-conflict saved sessions. **A session file does not store the stop reason or the HTTP budget**, so this page cannot show how the last search ended (see gap G1 below).',
      toSearch: 'New search →',
      toResults: 'Current project results →',
      toSettings: 'Settings & diagnostics →',

      /* ---------------------------------------------------------- session cards */
      gridTitle: 'Saved sessions',
      gridNote: 'The numbers on each card come from the session file itself; "sample data" marks a project built from synthetic demo records, not real literature.',
      current: 'Current project',
      continueAction: 'Continue',
      restore: 'Restore session',
      saveAs: 'Save as',
      restoreToast: 'Prototype: restoring a session loads its checkpoints, corpus and screening progress in stage two ({topic}).',
      saveAsToast: 'Prototype: Save as writes a session copy in stage two, after session_schema validation ({topic}).',
      papersLabel: 'Papers',
      papersUnit: '{n} papers',
      phaseLabel: 'Phase',
      phaseScreening: 'Screening & PRISMA',
      phaseSearch: 'Systematic search',
      phaseExpand: 'Citation expansion',
      phaseUnknown: 'Unknown',
      updatedLabel: 'Updated',
      schemaLabel: 'Session schema',
      projectIdLabel: 'project_id',
      directionLabel: 'Research direction',
      noDirection: 'No research direction recorded',
      screeningLabel: 'Screening status',
      screenNotStarted: 'Not started',
      screenPreliminary: 'Preliminary included (full text pending)',
      screenFinal: 'Finally included',
      screenExcluded: 'Finished with nothing included',
      stopHiddenNote: 'No stop reason is shown on the cards: a session file does not store stop_reason, so it comes back empty — see gap G1 below. A stop reason only appears in live run-time views (job cards, the citation-expansion page).',

      /* ---------------------------------------------------------- corrupt */
      corruptTitle: 'Corrupt sessions',
      corruptNote: 'Sessions that fail validation or cannot be read are listed separately instead of being mixed into the usable projects.',
      corruptBadge: 'Corrupt',
      corruptReasonLabel: 'Failure reason',
      corruptBackupLabel: 'Backup file',
      corruptPolicy: 'Back up first, never modify the original file, never break autosave; a future schema version is refused rather than downgraded.',
      corruptNoContinue: 'This card has no Continue button: a corrupt session is never silently repaired and never read in a downgraded mode.',

      /* ---------------------------------------------------------- gap G1 */
      gapTitle: 'Known gap G1: sessions do not store the stop reason or the HTTP budget',
      gapBody: 'persistence.state_to_dict() writes only 17 top-level keys and **omits stop_reason, http_budget and run_history**; after a restore, stop_reason falls back to an empty string. So when a session is restored from the project list, the interface cannot know how the last search ended — saturated, round-capped or budget-exhausted all look the same. That is precisely the property v0.9.5 insists on: never report a failure as completed coverage. This is why the cards show **no** stop reason: the field exists in the data, but after a restore it is run-time knowledge that was lost, and printing it would pretend the lost fact is known.',
      gapFix: 'Suggested fix in stage two: add stop_reason / http_budget to state_to_dict and to the schema; or accept explicitly that run-time facts are not persisted and render them as "not knowable for this run" instead of empty.',

      /* ---------------------------------------------------------- import */
      importTitle: 'Import a session',
      importNote: 'An import must pass **session_schema validation**: a future schema version is **refused**, never downgraded, and a corrupt file is backed up before anything else happens.',
      importPick: 'Choose a session file',
      importNone: 'No file selected',
      importChosen: 'Selected: {name}',
      importToast: 'Prototype: session_schema validation of {name} happens in stage two; this page does not read file contents.',
      importStatic: 'Static prototype: file contents are never read and nothing is written to disk.',
      importHint: 'Accepted extensions: .json / .session / .leext (session_schema decides in the end).',

      /* ---------------------------------------------------------- conflict */
      conflictTitle: 'Write conflicts',
      conflictBadge: 'Conflict detection active',
      conflictBody: 'project_id and the write-conflict check still hold: two browsers or processes writing the same project_id are detected instead of silently overwriting each other.',
      conflictNote: 'Order of handling: back up the existing file first, then let the user choose which copy to keep; never auto-merge and never overwrite silently.',

      /* ---------------------------------------------------------- offline demo */
      demoTitle: 'Offline demo project',
      demoBody: 'Creates an isolated project from an explicitly labelled SYNTHETIC DEMO sample: fully separate from existing sessions and never written into a real project.',
      demoBtn: 'Create offline demo project',
      demoToast: 'Prototype: stage two creates an isolated project from the SYNTHETIC DEMO sample and opens it.',

      empty: 'No saved sessions.',
    },
  },
});
