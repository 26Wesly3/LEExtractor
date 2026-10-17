// @vitest-environment jsdom
import { afterEach,beforeEach,describe,expect,it,vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { createApp,nextTick } from 'vue'
import { createMemoryHistory,createRouter } from 'vue-router'
import App from './App.vue'
import AppShell from './shell/AppShell.vue'
import { createWorkspaceTheme } from './bootstrap.js'

// 旧工作台的页面在 S1 之后只以重定向形式存在（src/router.js）。这些用例测的是
// App.vue 自己的页面逻辑，所以在这里保留一张本地旧表，并把每个路径都直接挂到
// App.vue（S2 起外壳由 AppShell 提供）。S6 删除旧分支时一并删除。
function createLegacyRouter(history){
  const routes=[{path:'/',name:'home',component:App},{path:'/search',name:'search',component:App},{path:'/projects',name:'projects',component:App},{path:'/settings',name:'settings',component:App},...['search','results','expand','landscape','questions','review','export'].map(name=>({path:`/projects/:projectId/${name}`,name:`project-${name}`,component:App})),{path:'/projects/:projectId/papers/:paperKey',name:'paper',component:App},{path:'/:pathMatch(.*)*',redirect:'/'}]
  return createRouter({history,routes,scrollBehavior:()=>({top:0})})
}

const pid='d'.repeat(32),key='a'.repeat(64),secondKey='b'.repeat(64),jid='c'.repeat(32)
const basePaper={paper_key:key,canonical_id:'10.1000/observed-record',title:'Evidence retained from a provider',abstract:'A complete abstract supplied by the backend.',authors:[{name:'Researcher A'}],year:2024,venue:'Test Journal',doi:'10.1000/observed-record',citation_count:12,reference_count:2,source:'openalex',providers:['openalex'],topics:[],url:'https://example.org/paper',relevance_score:.75,score_context_id:'ctx-1',score_breakdown:{lexical:.75},discovery_traces:[{method:'keyword',provider:'openalex',query:'evidence',seed_id:'',round_no:0,score:.75,evidence_ids:[],timestamp:'2026-10-09T10:00:00Z'}],screening:{title_decision:'pending',full_text_decision:'pending',full_text_retrieved:false,retrieval_attempted:false,reason:'',full_text_reason:'',status:'not_started',requires_manual_review:false}}
let app,router,project,paperRows,job,requests,unexpected,errors,domHost,scholarConfigured,styleHost
const workspaceStyles=readFileSync('src/styles.css','utf8')
const inertDescriptor=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'inert')
function response(data,status=200){return {ok:status<400,status,statusText:status<400?'OK':'Error',json:async()=>structuredClone(data)}}
// 运行概况的模拟数据：默认"统计尚未开始"（count 为 null），测试按需赋值。
let statsData={home_views:null,search_entry_clicks:null,search_runs:0,records:0}
let statsPostFails=false
function statsResponse(){return {
  launch:{date:'2026-10-12',days:12,precise_time:false},
  runtime:{process_uptime_seconds:42},
  home_views:{count:statsData.home_views,counting_since:statsData.home_views===null?null:'2026-10-12T00:00:00Z'},
  search_entry_clicks:{count:statsData.search_entry_clicks,counting_since:statsData.search_entry_clicks===null?null:'2026-10-12T00:00:00Z'},
  search_runs:{count:statsData.search_runs},records:{count:statsData.records},persisted:true}}
async function settle(){for(let i=0;i<80;i++){await Promise.resolve();await nextTick()}}
function button(text){return [...document.querySelectorAll('button,a.v-btn')].find(el=>el.textContent.trim()===text)}
function input(label){const target=[...document.querySelectorAll('label')].find(el=>el.textContent.trim()===label && el.getAttribute('for'));expect(target,`missing label ${label}`).toBeTruthy();return document.getElementById(target.getAttribute('for'))}
async function click(text){const target=button(text);expect(target,`missing button ${text}`).toBeTruthy();target.click();await settle()}
async function mount(path){router=createLegacyRouter(createMemoryHistory());await router.push(path);await router.isReady();app=createApp(AppShell);app.config.errorHandler=(err)=>errors.push(err.message);domHost=document.createElement('div');document.body.appendChild(domHost);app.use(router).use(createWorkspaceTheme()).mount(domHost);await settle()}
async function navigate(path){await router.push(path);await settle()}
function mockServer(url,options={}){
  const parsed=new URL(url,'http://localhost'),path=parsed.pathname,method=options.method||'GET',body=options.body?JSON.parse(options.body):undefined
  requests.push({path,method,body,query:parsed.searchParams})
  if(path==='/api/projects'&&method==='GET')return response({items:[project],total:1})
  if(path==='/api/projects'&&method==='POST'){project={...project,demo:false,name:body.name,topic:body.topic,research_direction:body.research_direction};return response(project,201)}
  if(path==='/api/demo/projects'&&method==='POST'){project.demo=true;return response(project,201)}
  if(path===`/api/projects/${pid}`)return response(project)
  if(path===`/api/projects/${pid}/papers`){const q=parsed.searchParams.get('q')||'';const rows=q?paperRows.filter(p=>p.title.toLowerCase().includes(q.toLowerCase())):paperRows;return response({items:rows,total:rows.length,page:1,page_size:10,score_context_id:'ctx-1',revision:project.revision})}
  if(path===`/api/projects/${pid}/facets`)return response({years:[{value:2024,count:1}],providers:[{value:'openalex',count:1}],methods:[{value:'keyword',count:1}],statuses:[{value:'not_started',count:1}]})
  if(path===`/api/projects/${pid}/papers/${key}`)return response({...basePaper,history:[],conflicts:[],reference_ids:[],citation_ids:[],relations:[]})
  if(path===`/api/jobs/${jid}`)return response(job)
  if(path===`/api/jobs/${jid}/cancel`){job={...job,cancel_requested:true};return response(job)}
  if(path===`/api/projects/${pid}/query-plan`)return response({topic:body.topic,direction:body.research_direction,translation:{original:body.research_direction,translated:'multi-agent collaboration in computer vision',engine:'local'},queries:{openalex:{query:'multi-agent collaboration computer vision'}},warnings:[],degraded:false})
  if(path===`/api/projects/${pid}/search`){project.active_job_id=jid;return response(job,202)}
  if(path===`/api/projects/${pid}/landscape`)return response({graph:{nodes:[{paper_key:key,canonical_id:basePaper.canonical_id,title:basePaper.title,year:2024,relevance_score:.75,score_context_id:'ctx-1'},{paper_key:secondKey,canonical_id:'10.1000/second-record',title:'Second observed record',year:2023,relevance_score:.5,score_context_id:'ctx-1'}],edges:[{source:key,target:secondKey,edge_type:'text_similarity',score:.8,witness_count:0,evidence:[]}],edge_type_counts:{citation:0,bibliographic_coupling:0,co_citation:0,text_similarity:1},summary:{communities:[[key]]}},landscape:{paper_count:1,topics:{topics:[],usable:false,note:'Too few papers'},temporal:{timeline:{2024:1}},novelty:{rows:[]},coverage:{topics:[],gaps:[],balance:0},scope:'current corpus'},score_context_id:'ctx-1'})
  if(path===`/api/projects/${pid}/questions`)return response({questions:[{kind:'coverage_gap',question:'Is this topic under-retrieved?',rationale:'Current corpus coverage is thin.',status:'hypothesis',evidence_strength:'weak',supporting_papers:[{paper_key:key,paper_id:basePaper.canonical_id,title:basePaper.title,year:2024}],risks:'The corpus can be incomplete',suggested_next_search:'evidence gap'}],usable:true,counts:{coverage_gap:1},citation_coverage:{complete:false},note:'Bounded by sample'})
  if(path===`/api/projects/${pid}/review`)return response({facts:{threshold_calibrated:false,calibration:{status:'not_calibrated',threshold:null}},prisma:{},ledger:[],queue:paperRows,records:[],revision:project.revision,score_context_id:'ctx-1'})
  if(path===`/api/projects/${pid}/exports`)return response({file_id:'e'.repeat(32),filename:'evidence_pack.zip',size:512,media_type:'application/zip',download_url:'/api/files/'+('e'.repeat(32))})
  if(path==='/api/settings'){if(method==='POST'&&body.serpapi_api_key)scholarConfigured=true;return response({providers:{semantic_scholar:{configured:false},openalex:{configured:true},google_scholar:{configured:scholarConfigured},unpaywall:{configured:false}},max_pdf_size_mib:50,local_only:true})}
  if(path==='/api/stats'&&method==='GET')return response(statsResponse())
  if(path==='/api/stats/home-view'&&method==='POST'){
    if(statsPostFails)return response({error:{message:'statistics unavailable',code:'unavailable'}},503)
    statsData.home_views=(statsData.home_views||0)+1;return response(statsResponse())}
  if(path==='/api/stats/search-entry-click'&&method==='POST'){
    if(statsPostFails)return response({error:{message:'statistics unavailable',code:'unavailable'}},503)
    statsData.search_entry_clicks=(statsData.search_entry_clicks||0)+1;return response(statsResponse())}
  if(path==='/api/health')return response({status:'ok',version:'0.9.9',ui:'vue',local_only:true,dependencies:{fastapi:true,algorithms:true}})
  unexpected.push({path,method,body});return response({error:{message:'Unexpected mock endpoint',detail:path}},404)
}
beforeEach(()=>{
  styleHost=document.createElement('style');styleHost.textContent=workspaceStyles;document.head.appendChild(styleHost)
  // Reflect the browser's boolean inert property, which jsdom does not provide.
  if(!inertDescriptor)Object.defineProperty(HTMLElement.prototype,'inert',{configurable:true,get(){return this.hasAttribute('inert')},set(value){this.toggleAttribute('inert',Boolean(value))}})
  localStorage.clear();sessionStorage.clear();document.body.innerHTML='';requests=[];unexpected=[];errors=[];scholarConfigured=true
  statsPostFails=false;statsData={home_views:null,search_entry_clicks:null,search_runs:0,records:0}
  project={project_id:pid,name:'Verified project',revision:7,created_at:'2026-10-09T10:00:00Z',updated_at:'2026-10-09T10:00:00Z',demo:false,topic:'evidence',research_direction:'Trace sources',phase:'systematic',score_context_id:'ctx-1',stop_reason:'',http_budget:{requests:0},run_history:[],search_manifest:{},screening:{},counts:{records_in_corpus:1,records_identified:1,reports_sought:0,reports_retrieved:0,records_final_included:0},active_job_id:null}
  paperRows=[structuredClone(basePaper)];job={job_id:jid,project_id:pid,kind:'search',status:'running',stop_reason:'',http_budget:{},progress:{stage:'search'},result:null,error:null,created_at:'2026-10-09T10:00:00Z',started_at:null,finished_at:null,cancel_requested:false}
  vi.stubGlobal('fetch',vi.fn(async(...args)=>mockServer(...args)))
  vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}})
  vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener(){},removeEventListener(){},addListener(){},removeListener(){}}))
  window.scrollTo=vi.fn();Element.prototype.scrollTo=vi.fn()
})
afterEach(()=>{app?.unmount();app=null;styleHost?.remove();if(!inertDescriptor)delete HTMLElement.prototype.inert;vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals();document.body.innerHTML='';expect(errors).toEqual([]);expect(unexpected).toEqual([])})
describe('workspace DOM and API integration',()=>{
  it.each([1280,390])('keeps paper actions above the scrim at viewport width %i and dismisses it cleanly',async width=>{
    vi.stubGlobal('innerWidth',width)
    await mount(`/projects/${pid}/papers/${key}`)
    const drawer=document.querySelector('.paper-drawer'),scrim=document.querySelector('.v-navigation-drawer__scrim')
    expect(drawer).toBeTruthy();expect(scrim).toBeTruthy()
    expect(drawer.parentElement).toBe(scrim.parentElement)
    expect(Number(getComputedStyle(drawer).zIndex)).toBeGreaterThan(Number(getComputedStyle(scrim).zIndex))
    expect(drawer.hasAttribute('inert')).toBe(false)
    // 详情抽屉的入场：容器 is-open，内容分段错峰推入（原型 drawer 的景深做法）
    expect(drawer.classList.contains('is-open')).toBe(true)
    const sections=[...drawer.querySelectorAll('.drawer-inner > *')]
    expect(sections.length).toBeGreaterThan(3)
    expect(getComputedStyle(sections[0]).animationName).toBe('le-drawer-in')
    expect(parseFloat(getComputedStyle(sections[1]).animationDelay)).toBeGreaterThan(0)
    await click('设为扩展种子');expect(button('取消种子选择')).toBeTruthy()
    const link=button('打开论文页面 ↗');expect(link.href).toBe(basePaper.url);expect(link.target).toBe('_blank')
    await click('关闭 ×');expect(router.currentRoute.value.fullPath).toBe(`/projects/${pid}/results`)
    // v0.9.10 的保证（路由立即切换、遮罩不再残留）不变；抽屉本体改为播完 260ms 退场动画后卸载。
    expect(document.querySelector('.paper-drawer')?.classList.contains('is-closing')).toBe(true)
    await new Promise(resolve=>setTimeout(resolve,350));await settle()
    expect(document.querySelector('.v-navigation-drawer__scrim')).toBeNull()
    expect(document.querySelector('.paper-drawer')).toBeNull()
    await navigate(`/projects/${pid}/papers/${key}`)
    const pushes=vi.spyOn(router,'push');document.querySelector('.v-navigation-drawer__scrim').click();await pushes.mock.results.at(-1).value;await settle()
    expect(router.currentRoute.value.fullPath).toBe(`/projects/${pid}/results`)
    await new Promise(resolve=>setTimeout(resolve,350));await settle()
    expect(document.querySelector('.paper-drawer')).toBeNull();expect(document.querySelector('.v-navigation-drawer__scrim')).toBeNull()
  })
  it('plays the cover departure and workspace arrival when the home wordmark is clicked',async()=>{
    const animate=vi.fn(()=>({finished:Promise.resolve(),cancel(){}}))
    Element.prototype.animate=animate
    try{
      await mount('/')
      document.querySelector('.hero-button').click()
      await settle()
      expect(router.currentRoute.value.fullPath).toBe('/search')
      expect(animate).toHaveBeenCalledTimes(2)
      const [departure,departureOptions]=animate.mock.calls[0]
      expect(departure[0].transform).toContain('scale(1)')
      expect(departure[1].transform).toContain('scale(.97)')
      expect(departureOptions.duration).toBe(280)
      const [arrival,arrivalOptions]=animate.mock.calls[1]
      expect(arrival[0].filter).toBe('blur(6px)')
      expect(arrival[0].transform).toContain('translateZ(-150px)')
      expect(arrivalOptions.duration).toBe(620)
    }finally{delete Element.prototype.animate}
  })
  it('leaves unconfigured Scholar unselected and saves its key without retaining the input',async()=>{
    scholarConfigured=false;await mount(`/projects/${pid}/search`)
    expect(input('Google Scholar').checked).toBe(false)
    await navigate('/settings')
    const secret=input('Google Scholar / SerpApi API Key');secret.value='fixture-key';secret.dispatchEvent(new Event('input',{bubbles:true}));await settle()
    await click('保存配置')
    expect(requests.find(r=>r.path==='/api/settings'&&r.method==='POST').body).toEqual({serpapi_api_key:'fixture-key'})
    expect(secret.value).toBe('');expect(document.body.textContent).not.toContain('fixture-key')
  })
  it('previews local translation, submits the edited query with semantic ranking, and clears stale translation',async()=>{
    await mount(`/projects/${pid}/search`)
    const focus=input('研究方向与关注点');focus.value='计算机视觉领域的多智能体合作问题';focus.dispatchEvent(new Event('input',{bubbles:true}));await settle()
    await click('翻译并查看检索计划')
    const translated=input('英文检索词／译文（可编辑，留空自动翻译）');expect(translated.value).toBe('multi-agent collaboration in computer vision')
    translated.value='cooperative visual perception';translated.dispatchEvent(new Event('input',{bubbles:true}));await settle()
    await click('开始检索')
    const body=requests.find(r=>r.path.endsWith('/search')&&r.method==='POST').body
    expect(body.search_keywords).toBe('cooperative visual perception');expect(body.translate).toBe(true);expect(body.ranking_mode).toBe('semantic');expect(body.providers).toContain('openreview');expect(body.providers).toContain('google_scholar')
    project.active_job_id=null;job.status='completed';await navigate(`/projects/${pid}/search`)
    const next=input('研究方向与关注点');next.value='自然语言处理';next.dispatchEvent(new Event('input',{bubbles:true}));await settle()
    expect(input('英文检索词／译文（可编辑，留空自动翻译）').value).toBe('')
  })
  it('sends only the selected provider and explains actual rate limits',async()=>{
    await mount(`/projects/${pid}/search`)
    for(const label of ['Semantic Scholar','OpenAlex','arXiv','OpenReview','Google Scholar']){input(label).click();await settle()}
    const pushes=vi.spyOn(router,'push');await click('开始检索');await pushes.mock.results.at(-1).value;await settle()
    expect(requests.find(r=>r.path.endsWith('/search')&&r.method==='POST').body.providers).toEqual(['crossref'])
    project.search_manifest={selected_providers:['crossref'],provider_results:[{provider:'crossref',status:'rate_limited',paper_count:0,request_stats:{requests:1,rate_limited:1}}]}
    project.active_job_id=null;job.status='partial';await navigate('/settings')
    await navigate(`/projects/${pid}/search`)
    expect(document.querySelector('.provider-outcomes').textContent).toContain('来源限流')
    expect(document.querySelector('.provider-outcomes').textContent).toContain('冷却')
    expect(input('Crossref').checked).toBe(true)
    expect(input('OpenAlex').checked).toBe(false)
  })
  it('creates a server demo before showing results and opens an opaque-key detail route',async()=>{
    await mount('/');await click('创建离线演示项目')
    expect(requests.find(r=>r.path==='/api/demo/projects'&&r.method==='POST')).toBeTruthy()
    expect(router.currentRoute.value.fullPath).toBe(`/projects/${pid}/results`)
    expect(document.querySelector('.demo-banner').textContent).toContain('模拟数据')
    expect(document.querySelector('.paper-title').textContent).toBe(basePaper.title)
    const pushes=vi.spyOn(router,'push');document.querySelector('.paper-title').click();await pushes.mock.results.at(-1).value;await settle()
    expect(router.currentRoute.value.params.paperKey).toBe(key)
    expect(document.querySelector('.paper-drawer').textContent).toContain(basePaper.abstract)
    expect(requests.some(r=>r.path===`/api/projects/${pid}/papers/${key}`)).toBe(true)
    await click('关闭 ×');expect(router.currentRoute.value.fullPath).toBe(`/projects/${pid}/results`)
  })
  it('applies a user filter to the actual request and shows an empty response honestly',async()=>{
    await mount(`/projects/${pid}/results`)
    const queryInput=input('在标题和摘要中查找');queryInput.value='not retrieved';queryInput.dispatchEvent(new Event('input',{bubbles:true}));await settle();await click('应用')
    const last=requests.filter(r=>r.path.endsWith('/papers')).at(-1)
    expect(last.query.get('q')).toBe('not retrieved');expect(last.query.get('min_score')).toBe('0')
    expect(document.querySelector('.paper-title')).toBeNull();expect(document.body.textContent).toContain('没有符合条件的文献')
  })
  it('starts a revision-bound search and refreshes corpus facts only after the backend completes',async()=>{
    await mount(`/projects/${pid}/search`);const pushes=vi.spyOn(router,'push');await click('开始检索');await pushes.mock.results.at(-1).value;await settle()
    const start=requests.find(r=>r.path.endsWith('/search')&&r.method==='POST');expect(start.body.expected_revision).toBe(7);expect(start.body.mode).toBe('systematic');expect(start.body.providers).toEqual(['semantic_scholar','openalex','arxiv','crossref','openreview','google_scholar'])
    expect(document.querySelector('.job-panel').textContent).toContain('进行中')
    paperRows.push({...basePaper,paper_key:secondKey,title:'Newly completed backend record'});project.counts.records_in_corpus=2;project.revision=8;project.active_job_id=null;job={...job,status:'completed',progress:{stage:'finished',records:2},result:{records:2}}
    await new Promise(resolve=>setTimeout(resolve,1500));await settle()
    expect(document.querySelector('.job-panel').textContent).toContain('已完成');expect(document.body.textContent).toContain('Newly completed backend record')
    expect(document.querySelector('.metrics').textContent).toContain('2')
    expect(document.querySelector('.job-panel button')).toBeNull()
  })
  it('renders all eleven pages from official DTO shapes and generates the controlled export link',async()=>{
    await mount('/projects');const projectName=input('项目名称');projectName.value='Created through UI';projectName.dispatchEvent(new Event('input',{bubbles:true}));await settle();await click('创建项目')
    expect(requests.find(r=>r.path==='/api/projects'&&r.method==='POST')?.body.name).toBe('Created through UI')
    const paths=['/', '/search', '/projects', '/settings', ...['search','results','expand','landscape','questions','review','export'].map(p=>`/projects/${pid}/${p}`),`/projects/${pid}/papers/${key}`]
    for(const path of paths){await navigate(path);expect(document.querySelector('main')).toBeTruthy();expect(document.querySelector('.global-message [role="alert"]')).toBeNull()}
    await navigate(`/projects/${pid}/export`);await click('生成文件')
    const operation=requests.find(r=>r.path.endsWith('/exports')&&r.method==='POST');expect(operation.body).toEqual({format:'evidence_pack',scope:'corpus',paper_keys:[]})
    expect(document.querySelector('.download-link').getAttribute('href')).toBe('/api/files/'+('e'.repeat(32)))
  })
  it('keeps provider identifiers in the detail drawer and draws every relation edge as a curve',async()=>{
    await mount(`/projects/${pid}/papers/${key}`)
    const drawer=document.querySelector('.paper-drawer')
    expect(drawer.textContent).toContain(basePaper.doi)
    expect(drawer.textContent).toContain('参考文献数')
    expect(drawer.querySelectorAll('.meta-grid dd').length).toBeGreaterThanOrEqual(6)
    expect(drawer.querySelector('.score-table').textContent).toContain('词法合计')
    await navigate(`/projects/${pid}/landscape`)
    const host=()=>document.querySelector('.graph-canvas')
    expect(host()).toBeTruthy()
    expect(host().getAttribute('data-nodes')).toBe('2')
    const boxes=[...document.querySelectorAll('.layer-pick input[type="checkbox"]')]
    expect(boxes.length).toBe(4)
    const control=()=>[...document.querySelectorAll('.layer-pick .v-selection-control')].find(node=>node.textContent.includes('文本相似'))
    expect(control()).toBeTruthy()
    // 引文边为 0 时自动叠加可用的关系层，画布上才有连线
    expect(control().querySelector('input[type="checkbox"]').checked).toBe(true)
    expect(host().getAttribute('data-links')).toBe('1')
    control().querySelector('input[type="checkbox"]').click()
    await settle()
    expect(host().getAttribute('data-links')).toBe('0')
    expect(document.querySelector('.graph-legend').textContent).toContain('文本相似')
    expect(document.querySelector('.graph-legend').textContent).toContain('直接引文')
  })
  it('summarises a finished search in one line and keeps the diagnostics folded',async()=>{
    await mount(`/projects/${pid}/search`);const pushes=vi.spyOn(router,'push');await click('开始检索');await pushes.mock.results.at(-1).value;await settle()
    project.search_manifest={selected_providers:['crossref','semantic_scholar'],provider_results:[
      {provider:'crossref',status:'success',paper_count:24,ok:true,failed:false,cached:true,request_stats:{requests:0,cache_hits:24}},
      {provider:'semantic_scholar',status:'rate_limited',paper_count:0,ok:false,failed:true,request_stats:{requests:1,rate_limited:1}}]}
    project.active_job_id=null
    job={...job,status:'partial',progress:{stage:'finished'},finished_at:null,timings:{total:12.4,provider_total:9.8,ranking:1.2,query_planning:0.4,providers:{semantic_scholar:8.2,crossref:1.6}}}
    await new Promise(resolve=>setTimeout(resolve,1500));await settle()
    const summary=document.querySelector('[data-job-summary]')
    expect(summary).toBeTruthy()
    expect(summary.textContent).toContain('12.4s')
    expect(summary.textContent).toContain('2 个来源')
    expect(summary.textContent).toContain('1 成功')
    expect(summary.textContent).toContain('1 受限')
    expect(document.body.textContent).toContain('部分文献来源暂时受限')
    const diagnostics=document.querySelector('[data-job-diagnostics]')
    expect(diagnostics.textContent).toContain('来源抓取合计')
    expect(diagnostics.textContent).toContain('缓存命中')
    expect(diagnostics.textContent).toContain('来源限流')
  })
  it('offers discovery-method, screening-state and non-CCF filters on the results page',async()=>{
    await mount(`/projects/${pid}/results`)
    expect(input('发现方式').value).toBe('全部发现方式')
    expect(input('筛选状态').value).toBe('全部筛选状态')
    expect(input('CCF 会议／期刊级别（2026）').value).toBe('全部级别')
    // 截图反馈：来源与 CCF 级别两个下拉挤在一起 —— 现在统一 20px 留白
    const railInputs=[...document.querySelectorAll('.facet-rail .v-input')]
    expect(railInputs.length).toBeGreaterThanOrEqual(3)
    expect(parseFloat(getComputedStyle(railInputs[1]).marginTop)).toBeGreaterThanOrEqual(20)
  })
  it('renders the operating overview between the features and the recent projects, with real numbers',async()=>{
    statsData={home_views:128,search_entry_clicks:9,search_runs:4,records:231}
    await mount('/')
    const overview=document.querySelector('.overview-band')
    expect(overview).toBeTruthy()
    const bands=[...document.querySelectorAll('.home-band')]
    const features=bands.find(band=>band.querySelector('.feature'))
    const recent=bands.find(band=>band.textContent.includes('最近项目'))
    expect(features).toBeTruthy();expect(recent).toBeTruthy()
    expect(features.compareDocumentPosition(overview)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(overview.compareDocumentPosition(recent)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 四项指标：上线日期 / 网页点击次数 / 搜索运行次数 / 共检索得文献
    expect([...overview.querySelectorAll('.stat-label')].map(el=>el.textContent)).toEqual(['项目上线日期','网页点击次数','搜索运行次数','共检索得文献'])
    expect(overview.querySelector('[data-stat="launch-date"]').textContent).toBe('2026-10-12')
    expect(overview.querySelector('[data-stat="home-views"]').textContent).toBe('128')
    expect(overview.querySelector('[data-stat="search-runs"]').textContent).toBe('4')
    expect(overview.querySelector('[data-stat="records"]').textContent).toBe('231')
    // 数字来自服务端：首屏 GET + 一次首页访问计数（幂等 id）
    expect(requests.some(r=>r.path==='/api/stats'&&r.method==='GET')).toBe(true)
    const homeView=requests.find(r=>r.path==='/api/stats/home-view'&&r.method==='POST')
    expect(homeView).toBeTruthy();expect(homeView.body.entry_id).toBeTruthy()
  })
  it('shows — instead of inventing numbers, and statistics failure never blocks the page',async()=>{
    statsPostFails=true
    await mount('/')
    const overview=document.querySelector('.overview-band')
    expect(overview.querySelector('[data-stat="home-views"]').textContent).toBe('—')
    expect(overview.querySelector('[data-stat="launch-date"]').textContent).toBe('2026-10-12')
    expect(errors).toEqual([])
    // 统计接口 503 之后仍然可以正常进入检索入口。
    await click('开始检索')
    expect(router.currentRoute.value.fullPath).toBe('/search')
  })
  it('counts a search-entry click from the home hero exactly once',async()=>{
    statsData={home_views:1,search_entry_clicks:0,search_runs:0,records:0}
    await mount('/')
    document.querySelector('.hero-button').click()
    await settle()
    const posts=requests.filter(r=>r.path==='/api/stats/search-entry-click'&&r.method==='POST')
    expect(posts.length).toBe(1)
    expect(posts[0].body.entry_id).toBeTruthy()
  })
  it('lists milestones newest first and keeps planned work out of the completed group',async()=>{
    await mount('/')
    const items=[...document.querySelectorAll('[data-milestones] .milestone')]
    expect(items.length).toBe(5)
    expect(items[0].querySelector('.milestone-date').textContent).toBe('2026-10-12')
    expect(items[0].textContent).toContain('公网测试上线')
    expect(items[0].textContent).toContain('已完成')
    expect(items[1].querySelector('.milestone-date').textContent).toBe('2026-10-10')
    expect(items[2].textContent).toContain('Web 工作台接入')
    expect(items[4].classList.contains('planned')).toBe(true)
    expect(items[4].textContent).toContain('后续计划')
    expect(items[4].textContent).toContain('稳定性加固')
    expect(items[0].querySelector('details')).toBeTruthy()
  })
})
