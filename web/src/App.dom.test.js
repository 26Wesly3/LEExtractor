// @vitest-environment jsdom
import { afterEach,beforeEach,describe,expect,it,vi } from 'vitest'
import { createApp,nextTick } from 'vue'
import { createMemoryHistory } from 'vue-router'
import App from './App.vue'
import { createWorkspaceRouter,createWorkspaceTheme } from './bootstrap.js'

const pid='d'.repeat(32),key='a'.repeat(64),secondKey='b'.repeat(64),jid='c'.repeat(32)
const basePaper={paper_key:key,canonical_id:'10.1000/observed-record',title:'Evidence retained from a provider',abstract:'A complete abstract supplied by the backend.',authors:[{name:'Researcher A'}],year:2024,venue:'Test Journal',doi:'10.1000/observed-record',citation_count:12,reference_count:2,source:'openalex',providers:['openalex'],topics:[],url:'https://example.org/paper',relevance_score:.75,score_context_id:'ctx-1',score_breakdown:{lexical:.75},discovery_traces:[{method:'keyword',provider:'openalex',query:'evidence',seed_id:'',round_no:0,score:.75,evidence_ids:[],timestamp:'2026-10-09T10:00:00Z'}],screening:{title_decision:'pending',full_text_decision:'pending',full_text_retrieved:false,retrieval_attempted:false,reason:'',full_text_reason:'',status:'not_started',requires_manual_review:false}}
let app,router,project,paperRows,job,requests,unexpected,errors,domHost
function response(data,status=200){return {ok:status<400,status,statusText:status<400?'OK':'Error',json:async()=>structuredClone(data)}}
async function settle(){for(let i=0;i<80;i++){await Promise.resolve();await nextTick()}}
function button(text){return [...document.querySelectorAll('button,a.v-btn')].find(el=>el.textContent.trim()===text)}
function input(label){const target=[...document.querySelectorAll('label')].find(el=>el.textContent.trim()===label);expect(target,`missing label ${label}`).toBeTruthy();return document.getElementById(target.getAttribute('for'))}
async function click(text){const target=button(text);expect(target,`missing button ${text}`).toBeTruthy();target.click();await settle()}
async function mount(path){router=createWorkspaceRouter(createMemoryHistory());await router.push(path);await router.isReady();app=createApp(App);app.config.errorHandler=(err)=>errors.push(err.message);domHost=document.createElement('div');document.body.appendChild(domHost);app.use(router).use(createWorkspaceTheme()).mount(domHost);await settle()}
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
  if(path===`/api/projects/${pid}/query-plan`)return response({topic:body.topic,direction:body.research_direction,queries:{openalex:{query:body.topic}},warnings:[],degraded:false})
  if(path===`/api/projects/${pid}/search`){project.active_job_id=jid;return response(job,202)}
  if(path===`/api/projects/${pid}/landscape`)return response({graph:{nodes:[{paper_key:key,canonical_id:basePaper.canonical_id,title:basePaper.title,year:2024,relevance_score:.75,score_context_id:'ctx-1'}],edges:[],edge_type_counts:{citation:0,bibliographic_coupling:0,co_citation:0,text_similarity:0},summary:{communities:[[key]]}},landscape:{paper_count:1,topics:{topics:[],usable:false,note:'Too few papers'},temporal:{timeline:{2024:1}},novelty:{rows:[]},coverage:{topics:[],gaps:[],balance:0},scope:'current corpus'},score_context_id:'ctx-1'})
  if(path===`/api/projects/${pid}/questions`)return response({questions:[{kind:'coverage_gap',question:'Is this topic under-retrieved?',rationale:'Current corpus coverage is thin.',status:'hypothesis',evidence_strength:'weak',supporting_papers:[{paper_key:key,paper_id:basePaper.canonical_id,title:basePaper.title,year:2024}],risks:'The corpus can be incomplete',suggested_next_search:'evidence gap'}],usable:true,counts:{coverage_gap:1},citation_coverage:{complete:false},note:'Bounded by sample'})
  if(path===`/api/projects/${pid}/review`)return response({facts:{threshold_calibrated:false,calibration:{status:'not_calibrated',threshold:null}},prisma:{},ledger:[],queue:paperRows,records:[],revision:project.revision,score_context_id:'ctx-1'})
  if(path===`/api/projects/${pid}/exports`)return response({file_id:'e'.repeat(32),filename:'evidence_pack.zip',size:512,media_type:'application/zip',download_url:'/api/files/'+('e'.repeat(32))})
  if(path==='/api/settings')return response({providers:{semantic_scholar:{configured:false},openalex:{configured:true},unpaywall:{configured:false}},max_pdf_size_mib:50,local_only:true})
  if(path==='/api/health')return response({status:'ok',version:'0.9.7',local_only:true,dependencies:{fastapi:true,algorithms:true}})
  unexpected.push({path,method,body});return response({error:{message:'Unexpected mock endpoint',detail:path}},404)
}
beforeEach(()=>{
  localStorage.clear();sessionStorage.clear();document.body.innerHTML='';requests=[];unexpected=[];errors=[]
  project={project_id:pid,name:'Verified project',revision:7,created_at:'2026-10-09T10:00:00Z',updated_at:'2026-10-09T10:00:00Z',demo:false,topic:'evidence',research_direction:'Trace sources',phase:'systematic',score_context_id:'ctx-1',stop_reason:'',http_budget:{requests:0},run_history:[],search_manifest:{},screening:{},counts:{records_in_corpus:1,records_identified:1,reports_sought:0,reports_retrieved:0,records_final_included:0},active_job_id:null}
  paperRows=[structuredClone(basePaper)];job={job_id:jid,project_id:pid,kind:'search',status:'running',stop_reason:'',http_budget:{},progress:{stage:'search'},result:null,error:null,created_at:'2026-10-09T10:00:00Z',started_at:null,finished_at:null,cancel_requested:false}
  vi.stubGlobal('fetch',vi.fn(async(...args)=>mockServer(...args)))
  vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}})
  vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener(){},removeEventListener(){},addListener(){},removeListener(){}}))
  window.scrollTo=vi.fn();Element.prototype.scrollTo=vi.fn()
})
afterEach(()=>{app?.unmount();app=null;vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals();document.body.innerHTML='';expect(errors).toEqual([]);expect(unexpected).toEqual([])})
describe('workspace DOM and API integration',()=>{
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
    const start=requests.find(r=>r.path.endsWith('/search')&&r.method==='POST');expect(start.body.expected_revision).toBe(7);expect(start.body.mode).toBe('systematic');expect(start.body.providers).toBeUndefined()
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
})
