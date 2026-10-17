<script setup>
import { computed,nextTick,ref,watch,onMounted,onBeforeUnmount } from 'vue'
import { useDisplay } from 'vuetify'
import { useRoute,useRouter } from 'vue-router'
import { request,post,queryString,isActiveJob,paperKey,safeUrl,acceptsJobSnapshot } from './api.js'
import { EAGLE_GRID } from './eagle-grid.js'
import HomeOverview from './HomeOverview.vue'
import { pageEntryId, recordSearchEntryClick } from './stats.js'
import ForceGraph from 'force-graph'
// 语言与「减少动态效果」由外壳统一持有（S2 从本文件提取，见 shell/prefs.js）。
import { lang,reduced } from './shell/prefs.js'
const route=useRoute(),router=useRouter()
const {width:viewportWidth}=useDisplay()
const drawerWidth=computed(()=>viewportWidth.value<960?viewportWidth.value:Math.min(880,Math.max(400,viewportWidth.value/2)))
const t=(zh,en)=>lang.value==='zh'?zh:en
const page=computed(()=>route.name==='paper'?'paper':String(route.name||'home').replace('project-',''))
const projectId=computed(()=>String(route.params.projectId||'')),project=ref(null),projects=ref([]),job=ref(null)
const busy=ref(false),loading=ref(false),error=ref(''),notice=ref(''),navOpen=ref(false),projectError=ref(false)
// 出入动画需要"关闭中"状态：v-if 只能瞬间卸载，无法播退场（样式见 styles.css 的 .is-closing）
const navClosing=ref(false),drawerClosing=ref(false),entering=ref(false)
let navTimer=null,drawerTimer=null
const papers=ref([]),total=ref(0),facets=ref({}),detail=ref(null),landscape=ref(null),questions=ref(null),review=ref(null),settings=ref(null),health=ref(null),plan=ref(null),exportFile=ref(null)
const filters=ref({q:'',provider:'',method:'',status:'',ccf_rank:'',year_from:'',year_to:'',min_score:0,sort:'relevance',page:1,page_size:10})
const selected=ref([]),query=ref(''),direction=ref(''),name=ref(''),yearFrom=ref(2020),yearTo=ref(new Date().getFullYear()),maxPapers=ref(100),providers=ref(['semantic_scholar','openalex','arxiv','crossref','openreview']),mode=ref('systematic')
const englishQuery=ref(''),rankingMode=ref('semantic')
const rounds=ref(2),expansionMax=ref(100),budget=ref(120),resume=ref(false)
const reviewStage=ref('title_abstract'),decisionReason=ref(''),readConfirmed=ref(false),calibrationLabel=ref('relevant'),calibrationLabels=ref([])
const exportFormat=ref('evidence_pack'),exportScope=ref('corpus'),saveName=ref(''),uploadInput=ref(null)
const keyInputs=ref({s2_api_key:'',openalex_api_key:'',serpapi_api_key:'',unpaywall_email:''}),credentialsMessage=ref('')
const drawerOpen=ref(false),graphLayers=ref({citation:true,bibliographic_coupling:false,co_citation:false,text_similarity:false}),pathStart=ref(''),pathEnd=ref(''),pathResult=ref(null),questionSelection=ref(null)
let timer=null,loadGeneration=0
const activeJob=computed(()=>isActiveJob(job.value))
const currentProjectPath=computed(()=>projectId.value?`/projects/${projectId.value}`:'')
const navigation=computed(()=>[
  ['search',t('检索','Search')],['results',t('文献结果','Results')],['expand',t('引文扩展','Expand')],['landscape',t('证据版图','Landscape')],['questions',t('候选问题','Questions')],['review',t('人工筛选','Review')],['export',t('导出与保存','Export')]
])
const title=computed(()=>({home:'LEExtractor',search:t('从研究问题开始','Start with a research question'),results:t('文献结果','Literature results'),paper:t('论文详情','Paper details'),expand:t('沿着证据继续探索','Follow the evidence'),landscape:t('研究证据版图','Research landscape'),questions:t('候选研究问题','Candidate questions'),review:t('人工筛选与标定','Screening and calibration'),export:t('导出与保存','Export and save'),projects:t('研究项目','Research projects'),settings:t('设置与诊断','Settings and diagnostics')})[page.value])
const lead=computed(()=>({search:t('描述方向，核对检索计划，再向真实来源发起检索。','Describe your focus, check the plan, then query the sources.'),results:t('比较文献、查看发现依据，并挑选下一轮扩展的种子。','Compare records, inspect discovery evidence and select expansion seeds.'),expand:t('向前追踪引用，向后追溯参考文献；每一轮保留发现轨迹。','Explore citations and references; retain evidence for every discovery.'),landscape:t('当前项目内的关系与分布，反映的是已获取的语料。','Relationships and distributions within the corpus acquired for this project.'),questions:t('从已有语料中整理的探索线索，需要阅读证据后人工判断。','Exploration leads derived from the corpus; assess them against the evidence.'),review:t('相关度用于排序，人工决定用于筛选；未获取全文不会自动排除。','Relevance supports ranking. Screening requires human decisions. Missing full text is not automatic exclusion.'),export:t('导出文献、发现轨迹和会话，便于复核与团队接手。','Export records, discovery evidence and the session for review and handoff.'),projects:t('恢复已保存的工作，或创建一个独立的研究项目。','Resume saved work or create an independent research project.'),settings:t('来源凭据保存在本机服务端，页面只显示配置状态。','Provider credentials stay on the local server; the browser displays configuration status only.')})[page.value]||'')
const sourceOptions=computed(()=>[{title:t('所有来源','All sources'),value:''},...(facets.value.providers||[]).map(row=>({title:sourceLabel(row.value),value:row.value}))])
const pages=computed(()=>Math.max(1,Math.ceil(total.value/filters.value.page_size)))
const paperOptions=computed(()=>papers.value.map(p=>({title:p.title,value:paperKey(p)})))
const questionItems=computed(()=>questions.value?.items||questions.value?.questions||[])
const edgeType=edge=>edge.edge_type||edge.relation_type||edge.type||edge.kind||'citation'
const relationEdges=computed(()=>{const g=landscape.value?.graph||landscape.value||{};const edges=g.edges||g.links||[];return edges.filter(edge=>graphLayers.value[edgeType(edge)])})
const graphNodes=computed(()=>landscape.value?.graph?.nodes||landscape.value?.nodes||[])
const GRAPH_MAX_NODES=24
const GRAPH_DUPLICATE_SCORE=0.995
const nodeKey=node=>node.paper_key||node.id
const allGraphEdges=computed(()=>{const g=landscape.value?.graph||landscape.value||{};return g.edges||g.links||[]})
// 近重复折叠：文本相似度达到阈值的两条记录其实是同一份文本（同一本书的各章、同一作品的重复来源记录）。
// 按并查集合并成单节点，避免用一整团 cosine=1 的边把图糊死，也避免同一份文本被画成十几个点。
const graphUnits=computed(()=>{const parent=new Map();graphNodes.value.forEach(node=>parent.set(nodeKey(node),nodeKey(node)));const find=key=>{let root=key;while(parent.get(root)!==undefined&&parent.get(root)!==root)root=parent.get(root);return root};const union=(a,b)=>{const ra=find(a),rb=find(b);if(ra!==rb)parent.set(rb,ra)};allGraphEdges.value.forEach(edge=>{if(edgeType(edge)!=='text_similarity'||Number(edge.score)<GRAPH_DUPLICATE_SCORE)return;const a=edge.source_key||edge.source,b=edge.target_key||edge.target;if(parent.has(a)&&parent.has(b))union(a,b)});const units=new Map();graphNodes.value.forEach(node=>{const root=find(nodeKey(node));if(!units.has(root))units.set(root,{id:root,label:node.title||node.label||root,records:[]});units.get(root).records.push(nodeKey(node))});return {units,unitOf:find}})
const graphUnitEdges=computed(()=>{const {unitOf}=graphUnits.value;const rows=[];allGraphEdges.value.forEach(edge=>{const source=unitOf(edge.source_key||edge.source),target=unitOf(edge.target_key||edge.target);if(source===undefined||target===undefined||source===target)return;rows.push({source,target,type:edgeType(edge),score:Number(edge.score)||0})});return rows})
const graphUnitDegrees=computed(()=>{const degrees=new Map();graphUnitEdges.value.forEach(({source,target})=>{degrees.set(source,(degrees.get(source)||0)+1);degrees.set(target,(degrees.get(target)||0)+1)});return degrees})
const graphLinkedUnits=computed(()=>[...graphUnits.value.units.values()].filter(unit=>(graphUnitDegrees.value.get(unit.id)||0)>0))
const graphIsolatedRecords=computed(()=>[...graphUnits.value.units.values()].filter(unit=>(graphUnitDegrees.value.get(unit.id)||0)===0).reduce((sum,unit)=>sum+unit.records.length,0))
const graphFoldedRecords=computed(()=>[...graphUnits.value.units.values()].reduce((sum,unit)=>sum+Math.max(0,unit.records.length-1),0))
const graphSelectedUnits=computed(()=>[...graphLinkedUnits.value].sort((left,right)=>(graphUnitDegrees.value.get(right.id)||0)-(graphUnitDegrees.value.get(left.id)||0)||right.records.length-left.records.length).slice(0,GRAPH_MAX_NODES))
const graphEdgeRows=computed(()=>{const enabled=new Set(graphSelectedUnits.value.map(unit=>unit.id));const seen=new Map();graphUnitEdges.value.forEach(edge=>{if(!graphLayers.value[edge.type]||!enabled.has(edge.source)||!enabled.has(edge.target))return;const key=[edge.source,edge.target,edge.type].sort().join('|');const previous=seen.get(key);if(!previous||previous.score<edge.score)seen.set(key,edge)});return [...seen.values()]})
const GRAPH_MAX_RELATION_EDGES=60
const visibleGraphEdges=computed(()=>{const rows=graphEdgeRows.value;const citation=rows.filter(edge=>edge.type==='citation').slice(0,160);const relations=rows.filter(edge=>edge.type!=='citation').sort((left,right)=>right.score-left.score).slice(0,GRAPH_MAX_RELATION_EDGES);return [...citation,...relations]})
const graphEdgeCounts=computed(()=>landscape.value?.graph?.edge_type_counts||{})
function edgeLabel(type){return {citation:t('直接引文','Citation'),bibliographic_coupling:t('书目耦合','Bibliographic coupling'),co_citation:t('共被引','Co-citation'),text_similarity:t('文本相似','Text similarity')}[type]||type}
const layerOptions=computed(()=>['citation','bibliographic_coupling','co_citation','text_similarity'].map(value=>({value,title:`${edgeLabel(value)} · ${Number(graphEdgeCounts.value[value]||0)}`})))
const graphNote=computed(()=>{if(!relationEdges.value.length){const available=Object.entries(graphEdgeCounts.value).filter(([,value])=>Number(value)>0).map(([key,value])=>`${edgeLabel(key)} ${value}`);return available.length?t(`当前勾选的关系层没有边。本样本可用的关系：${available.join('、')}。`,`No edges in the selected layers. Available in this sample: ${available.join(', ')}.`):t('本样本内还没有可计算的关系。先检索或做引文扩展。','No computable relations in this sample yet. Search or expand citations first.')}const onlyCitation=Object.keys(graphLayers.value).filter(key=>graphLayers.value[key]).join(',')==='citation';return onlyCitation?t('实线带箭头：直接引文。勾选其它关系层可在图中叠加推导关联。','Solid with arrow: citation. Tick another layer to overlay derived relations.'):t('直接引文用实线带箭头；推导关联用虚线分色，各层独立不合并加权。','Citation edges are solid with arrows; derived relations are dashed and colour-coded per layer, never merged.')})
// 图注必须解释「为什么图上的点比文献数少」：折叠了近重复、以及没有关系的记录不画。
const graphCapNote=computed(()=>{const parts=[];if(graphFoldedRecords.value)parts.push(t(`已折叠 ${graphFoldedRecords.value} 条近重复记录（同一文本，文本相似度 ≥ ${GRAPH_DUPLICATE_SCORE}）`,`${graphFoldedRecords.value} near-duplicate records folded (same text, similarity ≥ ${GRAPH_DUPLICATE_SCORE})`));if(graphIsolatedRecords.value)parts.push(t(`${graphIsolatedRecords.value} 条记录与本样本内任何记录都没有关系，不画在图上`,`${graphIsolatedRecords.value} records share no relation inside this sample and are not drawn`));parts.push(t(`图中 ${graphSelectedUnits.value.length} 个节点、${visibleGraphEdges.value.length} 条边（节点上限 ${GRAPH_MAX_NODES}，每层关系边上限 ${GRAPH_MAX_RELATION_EDGES}）`,`${graphSelectedUnits.value.length} nodes and ${visibleGraphEdges.value.length} edges drawn (node cap ${GRAPH_MAX_NODES}, ${GRAPH_MAX_RELATION_EDGES} per relation layer)`));return parts.join('；')+'。'})
const GRAPH_COMMUNITY_COLORS=['#1b1c20','#5b8c81','#8a6da4','#9a7a44','#556070','#707780']
const GRAPH_COMMUNITY_COLORS_DARK=['#c9c9d4','#7fbfae','#a98fc9','#c9a86a','#9aa2ad','#8f95a3']
const graphRank=computed(()=>{const rank=new Map();graphSelectedUnits.value.forEach((unit,index)=>rank.set(unit.id,index));return rank})
const graphCommunity=computed(()=>{const map=new Map();const groups=landscape.value?.graph?.summary?.communities||[];groups.forEach((group,index)=>(group||[]).forEach(member=>map.set(typeof member==='string'?member:(member.paper_key||member.id),index)));return map})
function unitCommunity(unit){for(const key of unit.records){const index=graphCommunity.value.get(key);if(index!==undefined)return index}return undefined}
function nodeFill(node){const index=node.community;const palette=graphDark.value?GRAPH_COMMUNITY_COLORS_DARK:GRAPH_COMMUNITY_COLORS;if(index===undefined)return graphDark.value?'#c9c9d4':'#1b1c20';return palette[index%palette.length]}
function nodeRadius(node){const rank=graphRank.value.get(node.id);return rank===undefined?6:rank<3?14:rank<8?10:7}
function syncGraphLayers(){const counts=landscape.value?.graph?.edge_type_counts||{};const layers={citation:true,bibliographic_coupling:false,co_citation:false,text_similarity:false};if(!Number(counts.citation||0))['bibliographic_coupling','co_citation','text_similarity'].forEach(key=>{if(Number(counts[key]||0)>0)layers[key]=true});graphLayers.value=layers}
const graphDark=ref(true),graphHost=ref(null)
let graphInstance=null,graphHoverKey=''
const GRAPH_LAYER_COLORS={citation:'#aab3bf',bibliographic_coupling:'#8a6da4',co_citation:'#5b8c81',text_similarity:'#9a7a44'}
const GRAPH_LAYER_COLORS_DARK={citation:'#767a8d',bibliographic_coupling:'#a98fc9',co_citation:'#7fbfae',text_similarity:'#c9a86a'}
const graphCanvasData=computed(()=>{const nodes=graphSelectedUnits.value.map(unit=>({id:unit.id,name:(unit.label||unit.id)+(unit.records.length>1?` ×${unit.records.length}`:''),r:nodeRadius(unit),community:unitCommunity(unit),records:unit.records})) ;nodes.forEach(node=>{node.fill=nodeFill(node)});const links=visibleGraphEdges.value.map(edge=>({source:edge.source,target:edge.target,type:edge.type,score:edge.score}));return {nodes,links}})
const graphNeighbours=computed(()=>{const map=new Map();graphCanvasData.value.links.forEach(({source,target})=>{if(!map.has(source))map.set(source,new Set());if(!map.has(target))map.set(target,new Set());map.get(source).add(target);map.get(target).add(source)});return map})
function graphColour(type){return (graphDark.value?GRAPH_LAYER_COLORS_DARK:GRAPH_LAYER_COLORS)[type]||'#aab3bf'}
function graphDimmed(node){if(!graphHoverKey)return false;const id=node&&typeof node==='object'?node.id:node;return graphHoverKey!==id&&!(graphNeighbours.value.get(graphHoverKey)||new Set()).has(id)}
function graphLinkActive(link){if(!graphHoverKey)return false;const source=link.source&&typeof link.source==='object'?link.source.id:link.source,target=link.target&&typeof link.target==='object'?link.target.id:link.target;return source===graphHoverKey||target===graphHoverKey}
// 布局与适配全部交给 force-graph：静态 warmup 出坐标 + 引擎停止后一次 zoomToFit，
// 不再自己算缩放/压缩坐标（那会和库的内部状态打架）。
function ensureGraph(){if(graphInstance)return graphInstance;const host=graphHost.value;if(!host)return null;const probe=document.createElement('canvas');if(!probe.getContext||!probe.getContext('2d'))return null;const instance=ForceGraph()(host).backgroundColor(graphDark.value?'#1e1e24':'#fcfcfd').nodeRelSize(6).nodeVal(node=>node.r).nodeLabel(node=>node.name).nodeColor(node=>graphDimmed(node)?'rgba(120,124,138,.18)':node.fill).linkColor(link=>graphLinkActive(link)||!graphHoverKey?graphColour(link.type):'rgba(120,124,138,.08)').linkWidth(link=>0.5+1.1*(Number(link.score)||1)).linkLineDash(link=>link.type==='citation'?null:[3,3]).linkDirectionalArrowLength(link=>link.type==='citation'?3:0).linkDirectionalArrowRelPos(0.92).d3VelocityDecay(0.3).warmupTicks(320).cooldownTicks(0).onEngineStop(()=>{if(graphInstance===instance)fitGraph(instance)}).onNodeHover(node=>{graphHoverKey=node?node.id:'';host.style.cursor=node?'pointer':'grab';instance.nodeColor(instance.nodeColor())}).onNodeClick(node=>openPaper({paper_key:node.records&&node.records[0]?node.records[0]:node.id}));instance.d3Force('charge').strength(-90);instance.d3Force('link').distance(link=>50+20*(1-(Number(link.score)||1)));graphInstance=instance;instance.graphData(graphCanvasData.value);return instance}
function renderGraph(){const instance=ensureGraph();if(!instance)return;const host=graphHost.value;instance.backgroundColor(graphDark.value?'#1e1e24':'#fcfcfd');if(host&&host.clientWidth)instance.width(host.clientWidth).height(host.clientHeight||460);instance.graphData(graphCanvasData.value);fitGraph(instance)}
function fitGraph(instance){try{const host=graphHost.value;const nodes=instance.graphData().nodes||[];if(!host||!nodes.length)return;const width=host.clientWidth||800,height=host.clientHeight||460,pad=60;const xs=nodes.map(node=>Number(node.x)||0),ys=nodes.map(node=>Number(node.y)||0);const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);const spanX=Math.max(1,maxX-minX),spanY=Math.max(1,maxY-minY);const scale=Math.min(3,Math.max(0.35,Math.min((width-2*pad)/spanX,(height-2*pad)/spanY)));instance.zoom(scale,0);instance.centerAt((minX+maxX)/2,(minY+maxY)/2,0)}catch(error){/* 画布未就绪时保持原视图 */}}
function graphZoom(factor){if(graphInstance)graphInstance.zoom(Math.min(8,Math.max(0.3,(graphInstance.zoom()||1)*factor)))}
function graphFit(){if(graphInstance)fitGraph(graphInstance)}
function stopGraph(){if(graphInstance&&typeof graphInstance._destructor==='function')graphInstance._destructor();graphInstance=null;graphHoverKey=''}
watch(graphCanvasData,async()=>{await nextTick();renderGraph()})
watch(graphDark,()=>renderGraph())
const eagleReady=ref(false),EAGLE_GLYPHS=['/','\\',':','·','—','+']
let eagleTimer=null,eagleFrame=0,eagleHovered=false,eagleState=null
function eagleReduced(){return reduced.value||(typeof window.matchMedia==='function'&&window.matchMedia('(prefers-reduced-motion: reduce)').matches)}
function eagleSetup(target){target.font='7px monospace';target.textBaseline='top';target.fillStyle='#647789'}
function eagleDraw(animate){const state=eagleState;if(!state)return;const {ctx,base,cells,width,height}=state;ctx.clearRect(0,0,width,height);ctx.drawImage(base,0,0,width,height);eagleSetup(ctx);cells.forEach(cell=>{if(cell.i%10!==0)return;const phase=eagleFrame*1.3+cell.i*.18;const dx=animate?Math.sin(phase)*2.4:0,dy=animate?Math.cos(phase*.7)*2.5:0;ctx.globalAlpha=animate?.55+.45*Math.abs(Math.sin(phase)):1;const glyph=animate&&Math.sin(phase)>.65?EAGLE_GLYPHS[(cell.i+Math.floor(eagleFrame*2))%EAGLE_GLYPHS.length]:cell.char;ctx.fillText(glyph,cell.x+dx,cell.y+dy)});for(let i=0;i<30;i++){const a=i*2.4,x=15+(i*43)%290,y=i%3===0?171+(i%5)*4:25+(i*23)%135;const edgeX=i%3===0?x:278+(i%4)*9;ctx.globalAlpha=.25+.22*Math.abs(Math.sin(a+(animate?eagleFrame*.6:0)));ctx.fillText(EAGLE_GLYPHS[(i+(animate?Math.floor(eagleFrame*.7):0))%EAGLE_GLYPHS.length],edgeX+(animate?Math.sin(eagleFrame*.8+a)*5:0),y+(animate?Math.cos(eagleFrame*.5+a)*5:0))}ctx.globalAlpha=1}
function eagleSync(){clearInterval(eagleTimer);eagleTimer=null;if(!eagleState)return;const animate=page.value==='home'&&!document.hidden&&!eagleHovered&&!eagleReduced();eagleDraw(animate);eagleReady.value=true;if(animate)eagleTimer=setInterval(()=>{eagleFrame+=.125;eagleDraw(true)},125)}
function eaglePause(){eagleHovered=true;eagleSync()}
function eagleResume(){eagleHovered=false;eagleSync()}
async function startEagle(){await nextTick();if(eagleState)return;const host=document.getElementById('eagle'),canvas=document.getElementById('eagleCanvas');if(!host||!canvas)return;const ctx=canvas.getContext('2d');if(!ctx)return;const width=320,height=200,ratio=Math.min(window.devicePixelRatio||1,2);canvas.width=width*ratio;canvas.height=height*ratio;ctx.scale(ratio,ratio);const base=document.createElement('canvas');base.width=canvas.width;base.height=canvas.height;const baseCtx=base.getContext('2d');if(!baseCtx)return;baseCtx.scale(ratio,ratio);eagleSetup(baseCtx);const cells=EAGLE_GRID.cells.map(([x,y],i)=>({x:12+x*4.4,y:10+y*4.7,char:x<EAGLE_GRID.cols/2?'\\':'/',i}));cells.forEach(cell=>{if(cell.i%10!==0)baseCtx.fillText(cell.char,cell.x,cell.y)});eagleState={ctx,base,cells,width,height};eagleHovered=false;host.addEventListener('pointerenter',eaglePause);host.addEventListener('pointerleave',eagleResume);document.addEventListener('visibilitychange',eagleSync);eagleSync()}
function stopEagle(){clearInterval(eagleTimer);eagleTimer=null;const host=document.getElementById('eagle');if(host){host.removeEventListener('pointerenter',eaglePause);host.removeEventListener('pointerleave',eagleResume)}document.removeEventListener('visibilitychange',eagleSync);eagleState=null;eagleReady.value=false}
watch(page,async value=>{if(value==='home')await startEagle();else stopEagle();if(value!=='landscape')stopGraph()},{immediate:true})
const topicRows=computed(()=>landscape.value?.landscape?.topics?.topics||[])
const temporal=computed(()=>landscape.value?.landscape?.temporal||{})
const timelineRows=computed(()=>Object.entries(temporal.value.timeline||{}))
const timelineMax=computed(()=>Math.max(1,...timelineRows.value.map(([,count])=>count)))
const coverage=computed(()=>landscape.value?.landscape?.coverage||{})
const novelty=computed(()=>landscape.value?.landscape?.novelty||{})
const referenceFacts=computed(()=>questions.value?.citation_coverage||{})
const knownProviders=['semantic_scholar','openalex','arxiv','crossref','openreview','google_scholar']
const normalizeProvider=source=>source==='s2'?'semantic_scholar':source
const plannedQueries=computed(()=>Object.fromEntries(Object.entries(plan.value?.provider_queries||plan.value?.queries||{}).filter(([source])=>providers.value.includes(normalizeProvider(source)))))
const manifestProviders=computed(()=>project.value?.search_manifest?.selected_providers||project.value?.search_manifest?.provider_results?.map(row=>row.provider)||Object.keys(project.value?.search_manifest?.provider_counts||{}))
const providerResults=computed(()=>(project.value?.search_manifest?.provider_results||[]).filter(row=>manifestProviders.value.map(normalizeProvider).includes(normalizeProvider(row.provider))))
const providerSummary=computed(()=>{const rows=providerResults.value;return{total:rows.length,ok:rows.filter(row=>row.ok).length,limited:rows.filter(row=>row.status==='rate_limited').length,failed:rows.filter(row=>row.failed&&row.status!=='rate_limited').length}})
const jobSeconds=computed(()=>{const created=job.value?.created_at,finished=job.value?.finished_at;if(created&&finished){const seconds=(new Date(finished)-new Date(created))/1000;if(Number.isFinite(seconds)&&seconds>0)return seconds}const total=Number(job.value?.timings?.total||0);return Number.isFinite(total)?total:0})
const timingRows=computed(()=>Object.entries(job.value?.timings||{}).filter(([,value])=>typeof value==='number'&&value>=0).sort((a,b)=>b[1]-a[1]))
// 持久摘要：任务面板是临时的，而 manifest 里的耗时会随项目保存，刷新后仍能自解释。
const manifestSeconds=computed(()=>Number(project.value?.search_manifest?.timings?.total||0))
const providerDiagnostics=computed(()=>(project.value?.search_manifest?.diagnostics||[]).filter(row=>manifestProviders.value.map(normalizeProvider).includes(normalizeProvider(row.source))))
const hasRateLimit=computed(()=>providerResults.value.some(row=>row.status==='rate_limited'||row.request_stats?.rate_limited>0))
const hasRetrievalFailure=computed(()=>providerResults.value.some(row=>row.failed===true||['rate_limited','timeout','provider_error','partial','budget_exhausted'].includes(row.status)))
function sourceLabel(source){return {s2:'Semantic Scholar',semantic_scholar:'Semantic Scholar',openalex:'OpenAlex',arxiv:'arXiv',crossref:'Crossref',openreview:'OpenReview',google_scholar:'Google Scholar',demo_fixture:t('模拟数据','Synthetic data')}[source]||source}
function display(value){return value===null||value===undefined||value===''?'—':String(value)}
function authors(paper){return (paper?.authors||[]).map(a=>typeof a==='string'?a:a.name).join(', ')}
function percent(value){return Number.isFinite(Number(value))?Number(value).toFixed(3):'—'}
function label(key){return ({queued:t('排队中','Queued'),running:t('进行中','Running'),completed:t('已完成','Completed'),partial:t('部分完成','Partial'),failed:t('失败','Failed'),canceled:t('已取消','Canceled'),interrupted:t('已中断','Interrupted'),saturated:t('满足饱和条件','Saturation rule met'),no_new_results:t('没有新增文献','No new records'),low_yield:t('新增数量较少','Low yield'),max_rounds:t('达到轮次上限','Round limit reached'),truncated:t('达到文献上限','Record limit reached'),budget_exhausted:t('预算已用尽','Budget exhausted'),api_failure:t('来源调用失败','Provider failure'),local_model_failure:t('本地模型加载失败','Local model unavailable'),local_embedding:t('正在加载本地语义模型','Loading local embedding model'),submission:t('投稿，录用未核实','Submission; acceptance unverified'),accepted:t('已录用，论文类型待核实','Accepted; paper type unverified'),rejected_or_withdrawn:t('拒稿或撤稿','Rejected or withdrawn'),published_metadata:t('已有出版元数据','Published metadata'),unknown:t('尚未核实','Unverified'),accept:t('纳入','Include'),reject:t('排除','Exclude'),maybe:t('待定','Maybe'),undecided:t('待筛选','Undecided'),pending:t('待筛选','Pending'),not_started:t('未开始','Not started'),hypothesis:t('待验证假设','Hypothesis'),no_open_access:t('无可获取开放全文','No available open full text'),download_failed:t('获取失败','Retrieval failed'),success:t('正常完成','Success'),success_empty:t('正常完成且零结果','Successful empty result'),rate_limited:t('来源限流','Rate limited'),timeout:t('来源超时','Provider timeout'),provider_error:t('来源错误','Provider error'),auth_error:t('来源鉴权失败（检查 API Key）','Provider auth error (check the API key)'),query_planning:t('查询规划','Query planning'),translation:t('本地翻译','Local translation'),embedding_load:t('语义模型加载','Embedding model load'),provider_total:t('来源抓取合计','Provider fetch total'),normalization:t('归一与去重','Normalisation'),ranking:t('相关度排序','Ranking'),persistence:t('落盘','Persistence'),total:t('合计','Total'),keyword:t('关键词检索','Keyword search'),backward_citation:t('引文回溯','Backward citation'),demo_fixture:t('模拟数据','Synthetic data'),uav:t('引文扩展','Citation expansion'),not_attempted:t('未尝试','Not attempted'),downloaded:t('已获取','Retrieved')})[key]||display(key)}
function messageFrom(err){const detail=err.detail;if(typeof detail==='string')return `${err.message} · ${detail}`;return err.message||String(err)}
async function action(task){if(busy.value)return;busy.value=true;error.value='';notice.value='';try{return await task()}catch(err){error.value=messageFrom(err)}finally{busy.value=false}}
async function projectRequest(suffix,options){if(!projectId.value)throw new Error(t('请先选择或创建项目','Select or create a project first'));return request(`/projects/${encodeURIComponent(projectId.value)}${suffix}`,options)}
async function projectPost(suffix,body={}){const revisionWrites=['/search','/expand','/similar','/screening-decisions','/calibration','/downloads','/save'];return projectRequest(suffix,{method:'POST',body:revisionWrites.includes(suffix)?{expected_revision:project.value?.revision,...body}:body})}
function navigate(name){closeNav();router.push(projectId.value?`${currentProjectPath.value}/${name}`:'/search')}
async function loadProject(){const data=await projectRequest('');project.value=data;projectError.value=false;saveName.value=data.name||'';return data}
async function loadPapers(){const data=await projectRequest(`/papers?${queryString(filters.value)}`);papers.value=data.items||[];total.value=data.total||0;return data}
async function loadPage(){const generation=++loadGeneration;loading.value=true;error.value='';projectError.value=false;if(!drawerClosing.value)detail.value=null;try{
  if(projectId.value){const data=await loadProject();if(generation!==loadGeneration)return;if(data.active_job_id)await followJob(data.active_job_id);else if(job.value?.project_id!==projectId.value){job.value=null;const previous=sessionStorage.getItem(`le.job.${projectId.value}`);if(previous){try{await followJob(previous)}catch{sessionStorage.removeItem(`le.job.${projectId.value}`)}}}
    if(['results','expand','review','export'].includes(page.value))await loadPapers();
    if(page.value==='results')facets.value=await projectRequest('/facets');
    if(page.value==='search'){query.value=String(route.query.q||data.topic||'');direction.value=String(route.query.direction||data.research_direction||'');name.value=data.name||'';const savedProviders=data.search_manifest?.selected_providers;if(Array.isArray(savedProviders)&&savedProviders.length)providers.value=savedProviders.map(normalizeProvider).filter(source=>knownProviders.includes(source))}
    if(page.value==='paper'){detail.value=await projectRequest(`/papers/${encodeURIComponent(String(route.params.paperKey))}`);drawerOpen.value=false;await nextTick();drawerOpen.value=true}
    if(page.value==='landscape'){landscape.value=await projectRequest('/landscape');syncGraphLayers()}
    if(page.value==='questions')questions.value=await projectRequest('/questions');
    if(page.value==='review')await loadReview();
  }else{project.value=null;job.value=null;if(page.value==='projects'||page.value==='home')projects.value=(await request('/projects')).items||[];
    if(page.value==='settings'){settings.value=await request('/settings');health.value=await request('/health')}
  }
 }catch(err){if(generation===loadGeneration){error.value=messageFrom(err);if(err.status===404&&projectId.value)projectError.value=true}}finally{if(generation===loadGeneration)loading.value=false}}
function jobId(data){return data.job_id||data.jobId||data.id}
async function followJob(id){if(!id)return;clearTimeout(timer);job.value=await request(`/jobs/${encodeURIComponent(id)}`);if(job.value.project_id===projectId.value)sessionStorage.setItem(`le.job.${projectId.value}`,id);schedulePoll()}
function schedulePoll(){clearTimeout(timer);if(activeJob.value)timer=setTimeout(pollJob,1200)}
async function pollJob(){if(!job.value)return;const id=jobId(job.value),boundProject=projectId.value;try{const updated=await request(`/jobs/${encodeURIComponent(id)}`);if(boundProject!==projectId.value||id!==jobId(job.value)||!acceptsJobSnapshot(job.value,updated,projectId.value))return;job.value=updated;if(isActiveJob(updated)){schedulePoll();return}await loadProject();if(['results','expand','review','export'].includes(page.value))await loadPapers();if(page.value==='review')await loadReview();notice.value=`${t('任务','Job')}: ${label(updated.status)}${updated.stop_reason?' · '+label(updated.stop_reason):''}`;if(updated.error)error.value=typeof updated.error==='string'?updated.error:updated.error.message}catch(err){error.value=messageFrom(err);clearTimeout(timer)}}
async function startJob(suffix,body){const result=await projectPost(suffix,body);await followJob(jobId(result));await loadProject();return result}
async function cancelJob(){await action(async()=>{job.value=await post(`/jobs/${encodeURIComponent(jobId(job.value))}/cancel`);schedulePoll();notice.value=t('已请求取消；任务将在安全检查点停止。','Cancellation requested. The job stops at the next safe checkpoint.')})}
async function ensureProject(){if(projectId.value)return projectId.value;const data=await post('/projects',{name:name.value.trim()||query.value.trim(),topic:query.value.trim(),research_direction:direction.value.trim()});const id=data.project_id;await router.push(`/projects/${encodeURIComponent(id)}/search`);await loadProject();return id}
function searchPayload(){return {query:query.value.trim(),research_direction:direction.value.trim(),year_from:Number(yearFrom.value),year_to:Number(yearTo.value),max_papers:Number(maxPapers.value),providers:[...providers.value],mode:mode.value,translate:true,search_keywords:englishQuery.value.trim(),ranking_mode:rankingMode.value,http_budget:Number(budget.value)}}
async function previewPlan(){await action(async()=>{if(!query.value.trim())throw new Error(t('请输入检索主题','Enter a search topic'));if(!providers.value.length)throw new Error(t('至少选择一个检索来源','Select at least one search provider'));await ensureProject();plan.value=await projectPost('/query-plan',{topic:query.value.trim(),research_direction:direction.value.trim(),translate:true,search_keywords:englishQuery.value.trim(),year_from:Number(yearFrom.value),year_to:Number(yearTo.value)});englishQuery.value=plan.value.translation?.translated||englishQuery.value})}
async function search(){await action(async()=>{if(!query.value.trim())throw new Error(t('请输入检索主题','Enter a search topic'));if(!providers.value.length)throw new Error(t('至少选择一个检索来源','Select at least one search provider'));if(Number(yearFrom.value)>Number(yearTo.value))throw new Error(t('起始年份不能大于结束年份','The starting year must precede the ending year'));await ensureProject();await startJob('/search',searchPayload());await router.push(`${currentProjectPath.value}/results`)})}
async function loadDemo(){await action(async()=>{const data=await post('/demo/projects');selected.value=[];filters.value.page=1;await router.push(`/projects/${encodeURIComponent(data.project_id)}/results`)})}
async function createProject(){await action(async()=>{if(!name.value.trim())throw new Error(t('请输入项目名称','Enter a project name'));const data=await post('/projects',{name:name.value.trim(),topic:query.value.trim(),research_direction:direction.value.trim()});await router.push(`/projects/${encodeURIComponent(data.project_id)}/search`)})}
async function filterChanged(){filters.value.page=1;await action(loadPapers)}
function toggleSeed(paper){const key=paperKey(paper);selected.value=selected.value.includes(key)?selected.value.filter(x=>x!==key):[...selected.value,key];saveSeeds()}
function saveSeeds(){if(projectId.value)sessionStorage.setItem(`le.seeds.${projectId.value}`,JSON.stringify(selected.value))}
function selectPage(){const keys=papers.value.map(paperKey);selected.value=[...new Set([...selected.value,...keys])];saveSeeds()}
function openPaper(paper){router.push(`${currentProjectPath.value}/papers/${encodeURIComponent(paperKey(paper))}`)}
function backToResults(){router.push(`${currentProjectPath.value}/results`)}
/* 菜单：纵向面板的出入动画（打开即插入，关闭先播 260ms 退场再卸载） */
function openNav(){clearTimeout(navTimer);navClosing.value=false;navOpen.value=true}
function closeNav(){if(!navOpen.value&&!navClosing.value)return;navOpen.value=false;navClosing.value=true;clearTimeout(navTimer);navTimer=setTimeout(()=>{navClosing.value=false},260)}
/* 菜单的三条退出路径：面板内 ×、点遮罩、Esc（遮罩见模板 .nav-scrim，样式与原型 dialog::backdrop 一致） */
function onGlobalKeydown(event){
  if(event.key!=='Escape')return
  if(drawerOpen.value){closeDrawer();return}
  if(navOpen.value||navClosing.value)closeNav()
}
onMounted(()=>document.addEventListener('keydown',onGlobalKeydown))
onBeforeUnmount(()=>document.removeEventListener('keydown',onGlobalKeydown))
function navModules(){return [['home',t('首页','Home')],...navigation.value,['projects',t('全部项目','All projects')]]}
function navDesc(key){return ({home:t('封面与最近项目','Cover and recent projects'),search:t('描述方向并核对检索计划','Describe the focus and check the plan'),results:t('比较文献与发现依据','Compare records and their evidence'),expand:t('沿引文与参考文献继续扩展','Follow citations and references'),landscape:t('语料关系与分布','Relations, clusters and distribution'),questions:t('从语料整理的探索线索','Exploration leads from the corpus'),review:t('人工纳入与排除决定','Human include and exclude decisions'),export:t('导出文献、轨迹与会话','Export records, evidence and session'),projects:t('恢复或新建研究项目','Resume or create a research project')})[key]||''}
/* 抽屉：关闭时先播退场再卸载（路由立即切换，遮罩不再拦截点击 —— v0.9.10 的缺陷保证不变） */
function closeDrawer(){drawerOpen.value=false;drawerClosing.value=true;clearTimeout(drawerTimer);drawerTimer=setTimeout(()=>{drawerClosing.value=false},260);backToResults()}
/* 首页：X 随指针柔和旋转（Demo v0.3 原算法）+ 点击字标的景深到场动画 */
let rotorAngle=0,rotorTarget=0,rotorFrame=0,rotorLast=0
function motionReduced(){return reduced.value||(typeof window.matchMedia==='function'&&window.matchMedia('(prefers-reduced-motion: reduce)').matches)}
function rotorTick(now){const el=document.getElementById('rotor');if(!el||page.value!=='home'||motionReduced()||document.hidden){rotorFrame=0;return}
  const dt=Math.min(rotorLast?now-rotorLast:16,48);rotorLast=now
  const diff=((rotorTarget-rotorAngle+540)%360)-180
  rotorAngle+=diff*(1-Math.exp(-dt/180));el.style.transform=`rotate(${rotorAngle}deg)`
  if(Math.abs(diff)>.08)rotorFrame=requestAnimationFrame(rotorTick);else{rotorFrame=0;rotorLast=0}}
function rotorWake(){if(!rotorFrame&&!motionReduced()&&page.value==='home'){rotorLast=0;rotorFrame=requestAnimationFrame(rotorTick)}}
function rotorMove(event){const el=document.getElementById('rotor');if(!el||page.value!=='home'||motionReduced()||event.pointerType==='touch')return
  const r=el.getBoundingClientRect();rotorTarget=Math.atan2(event.clientY-r.top-r.height/2,event.clientX-r.left-r.width/2)*180/Math.PI;rotorWake()}
function rotorReset(){rotorTarget=0;rotorWake()}
async function enterWorkspace(){
  // 首页主视觉入口也是一次"检索入口点击"；统计失败不影响进入检索。
  recordSearchEntryClick(pageEntryId()).catch(()=>null)
  if(motionReduced()||typeof document==='undefined'||typeof Element==='undefined'||!Element.prototype.animate){await router.push('/search');return}
  if(entering.value)return
  entering.value=true
  try{
    const cover=document.querySelector('.cover')
    if(cover){const out=cover.animate([{opacity:1,transform:'translateY(0) scale(1)'},{opacity:0,transform:'translateY(-12px) scale(.97)'}],{duration:280,easing:'cubic-bezier(.4,0,.8,1)',fill:'both'});await out.finished;out.cancel()}
    await router.push('/search');await nextTick()
    const main=document.querySelector('main')
    if(main){const arrival=main.animate([{opacity:0,transform:'perspective(1000px) translateZ(-150px) translateY(24px)',filter:'blur(6px)'},{opacity:1,transform:'perspective(1000px) translateZ(0) translateY(0)',filter:'blur(0px)'}],{duration:620,easing:'cubic-bezier(.16,1,.3,1)',fill:'both'});await arrival.finished;arrival.cancel()}
  }catch(e){/* 动画失败不阻断导航 */}
  entering.value=false}
function providerNames(paper){const list=(paper?.providers||[]).map(sourceLabel).filter(Boolean);return (list.length?list:[sourceLabel(paper?.source)]).filter(Boolean).join(' · ')||'—'}
function identifierText(paper){const ids=paper?.identifiers||{};const rows=[['OpenAlex',ids.openalex_id],['arXiv',ids.arxiv_id],['S2',ids.semantic_scholar_id],['PMID',ids.pmid],['OpenReview',ids.openreview_id],['DBLP',ids.dblp_key]].filter(([,value])=>value);return rows.length?rows.map(([name,value])=>`${name} ${value}`).join(' · '):(paper?.canonical_id||'—')}
function historyLine(row){if(!row||typeof row!=='object')return String(row);const parts=[row.at,row.stage?label(row.stage):'',row.decision?label(row.decision):'',row.reason].filter(Boolean);return parts.length?parts.join(' · '):JSON.stringify(row)}
const scoreRows=computed(()=>{const breakdown=detail.value?.score_breakdown||{};const labels={word:t('词法·词','Lexical · word'),character:t('词法·字符 3-5gram','Lexical · char 3-5gram'),coverage:t('查询词覆盖率','Query term coverage'),lexical:t('词法合计','Lexical total'),semantic:t('语义（本地 MiniLM）','Semantic (local MiniLM)'),concept_coverage:t('方向概念覆盖','Focus concept coverage'),semantic_uses_abstract:t('语义是否使用摘要','Semantic used abstract')};return Object.entries(breakdown).filter(([,value])=>typeof value==='number').map(([key,value])=>({key,label:labels[key]||key,value:Number(value).toFixed(3)}))})
async function expand(){await action(async()=>{if(!selected.value.length&&!resume.value)throw new Error(t('请先在结果页选择种子文献','Select seed papers in Results first'));await startJob('/expand',{seed_keys:selected.value,num_seeds:selected.value.length||5,max_rounds:Number(rounds.value),max_per_direction:Number(expansionMax.value),year_from:Number(yearFrom.value),year_to:Number(yearTo.value),http_budget:Number(budget.value),resume:resume.value})})}
async function similar(){await action(async()=>{if(!selected.value.length)throw new Error(t('请先选择种子文献','Select seed papers first'));await startJob('/similar',{seed_keys:selected.value,num_seeds:selected.value.length,top_k:Number(expansionMax.value),http_budget:Number(budget.value)})})}
async function decide(paper,decision){await action(async()=>{if(reviewStage.value==='full_text'&&decision==='accept'&&!readConfirmed.value)throw new Error(t('纳入全文前，请确认你已阅读全文','Confirm that you have read the full text before inclusion'));await projectPost('/screening-decisions',{paper_key:paperKey(paper),stage:reviewStage.value,decision,reason:decisionReason.value,read_confirmed:readConfirmed.value});await loadProject();await loadReview();await loadPapers();decisionReason.value='';readConfirmed.value=false;notice.value=t('人工决定已保存','Screening decision saved')})}
async function downloads(keys){await action(async()=>{const paper_keys=keys||selected.value;if(!paper_keys.length)throw new Error(t('请先选择文献','Select papers first'));await startJob('/downloads',{paper_keys})})}
function addLabel(paper){const key=paperKey(paper);calibrationLabels.value=calibrationLabels.value.filter(row=>row.paper_key!==key);calibrationLabels.value.push({paper_key:key,label:calibrationLabel.value});notice.value=t('标签已加入本次标定，提交后生效','Label added to this calibration. Submit to apply.')}
async function calibrate(){await action(async()=>{if(!calibrationLabels.value.length)throw new Error(t('请先添加人工标签','Add human labels first'));await projectPost('/calibration',{labels:calibrationLabels.value});await loadProject();await loadReview();notice.value=t('标定已保存，适用性由后端判定。','Calibration saved. Applicability is assessed by the backend.');calibrationLabels.value=[]})}
async function exportProject(){await action(async()=>{if(exportScope.value==='selected'&&!selected.value.length)throw new Error(t('请先选择文献','Select papers first'));exportFile.value=await projectPost('/exports',{format:exportFormat.value,scope:exportScope.value,paper_keys:selected.value});notice.value=t('导出文件已生成，请点击下载。','Export generated. Use the download link.')})}
async function save(){await action(async()=>{await projectPost('/save',{name:saveName.value||project.value.name,expected_revision:project.value.revision});await loadProject();notice.value=t('项目已保存','Project saved')})}
async function importSession(event){const file=event.target.files?.[0];if(!file)return;await action(async()=>{if(file.size>20*1024*1024)throw new Error(t('会话文件不能超过 20 MB','Session files must be at most 20 MB'));let session;try{session=JSON.parse(await file.text())}catch{throw new Error(t('请选择有效的 JSON 会话文件','Select a valid JSON session file'))}const data=await post('/projects/import',{session});selected.value=[];await router.push(`/projects/${encodeURIComponent(data.project_id)}/results`);notice.value=t('已导入为独立项目','Imported as an independent project')});event.target.value=''}
async function saveCredentials(){await action(async()=>{const payload=Object.fromEntries(Object.entries(keyInputs.value).filter(([,v])=>v.trim()).map(([k,v])=>[k,v.trim()]));if(!Object.keys(payload).length)throw new Error(t('请输入需要更新的凭据','Enter a credential to update'));keyInputs.value={s2_api_key:'',openalex_api_key:'',serpapi_api_key:'',unpaywall_email:''};settings.value=await post('/settings',payload);credentialsMessage.value=t('配置已更新','Configuration updated')})}
async function findPath(){await action(async()=>{if(!pathStart.value||!pathEnd.value)throw new Error(t('请选择路径起点和终点','Select start and end papers'));pathResult.value=await projectRequest(`/landscape/path?${queryString({source:pathStart.value,target:pathEnd.value})}`)})}
function restoreSeeds(){try{selected.value=JSON.parse(sessionStorage.getItem(`le.seeds.${projectId.value}`)||'[]')}catch{selected.value=[]}}
async function loadReview(){review.value=await projectRequest(`/review?stage=${reviewStage.value}`)}
const progressEntries=computed(()=>Object.entries(job.value?.progress||{}).filter(([,v])=>typeof v!=='object'))
watch(()=>route.fullPath,async()=>{clearTimeout(timer);closeNav();rotorReset();questionSelection.value=null;plan.value=null;exportFile.value=null;restoreSeeds();await loadPage()})
watch([query,direction],()=>{englishQuery.value='';plan.value=null})
watch(reviewStage,()=>{readConfirmed.value=false;if(page.value==='review')action(loadReview)})
onMounted(async()=>{restoreSeeds();await loadPage();try{settings.value=await request('/settings');if(settings.value.providers?.google_scholar?.configured&&!providers.value.includes('google_scholar'))providers.value.push('google_scholar')}catch{}})
onBeforeUnmount(()=>{clearTimeout(timer);stopEagle();stopGraph()})
</script>

<template>
    <div v-if="error" class="global-message"><v-alert type="error" variant="tonal" closable @click:close="error=''" role="alert">{{error}}</v-alert></div>
    <div v-if="notice" class="global-message"><v-alert type="info" variant="tonal" closable @click:close="notice=''" role="status">{{notice}}</v-alert></div>
    <div v-if="project?.demo" class="demo-banner">{{t('隔离演示项目 · 所有文献与关系为模拟数据，不可作为真实研究证据。','Isolated demo project · All records and relationships are synthetic, not research evidence.')}}</div>
    <div v-if="loading" class="loading-line" role="status"><v-progress-linear indeterminate color="primary" :height="2"/><span>{{t('正在读取项目…','Loading project…')}}</span></div>
    <section v-if="projectError" class="container empty"><h1>{{t('项目未找到','Project not found')}}</h1><p>{{t('这个项目可能已移除，请在项目列表中重新选择。','This project may have been removed. Select a project from the list.')}}</p><v-btn to="/projects" color="primary">{{t('查看项目','View projects')}}</v-btn></section>

    <template v-else-if="page==='home'">
      <section class="cover" :class="{'is-entering':entering}" @pointermove="rotorMove" @pointerleave="rotorReset"><button class="hero-button" @click="enterWorkspace()" :aria-label="t('开始探索','Start exploring')"><span class="wordmark"><span>LEE</span><span class="x-slot"><span id="rotor" class="rotor"/></span><span>tractor</span></span><span class="invitation">{{t('惟学无际，际于天地 · 点击开始探索','Knowledge without limits · Begin exploring')}}</span></button><div id="eagle" class="eagle" role="img" :aria-label="t('动态字符版求是鹰','Animated Qiushi eagle character art')"><canvas id="eagleCanvas" width="640" height="400" aria-hidden="true"/></div></section>
      <section class="home-band"><div class="logo-band"><span>{{t('参赛单位','Affiliation')}}</span><img src="/assets/zju-wordmark.png" :alt="t('浙江大学','Zhejiang University')"/><span class="home-tag">{{t('可追溯的文献探索','Traceable literature exploration')}}</span></div></section>
      <section class="home-band"><p class="eyebrow">LITERATURE · EVIDENCE · EXPLORATION</p><h2>{{t('让每一次探索，留下依据。','Keep the evidence behind every discovery.')}}</h2><p class="home-copy">{{t('从多来源检索走向引文扩展，在证据版图中比较方向，由你完成筛选与判断。记录与发现轨迹一起保存，研究过程可以复核。','Search across sources, expand citation networks and compare directions in an evidence landscape. Make your own screening decisions. Records and discovery trails remain available for review.')}}</p><div class="actions"><v-btn size="large" color="primary" to="/search" @click="recordSearchEntryClick(pageEntryId()).catch(()=>null)">{{t('开始检索','Start a search')}}</v-btn><v-btn size="large" variant="outlined" @click="loadDemo" :loading="busy">{{t('创建离线演示项目','Create offline demo')}}</v-btn><v-btn size="large" variant="text" to="/projects">{{t('恢复已有项目','Resume a project')}}</v-btn></div></section>
      <section class="home-band"><div class="cols-3"><article class="feature"><span>01 / DISCOVERY</span><h3>{{t('查找与扩展','Search and expand')}}</h3><p>{{t('检索来源独立计数，强标识去重，使用真实引文和参考文献继续探索。','Provider counts remain separate. Strong identifiers deduplicate records. Expand with observed citations and references.')}}</p></article><article class="feature"><span>02 / UNDERSTANDING</span><h3>{{t('关系与证据','Relationships and evidence')}}</h3><p>{{t('查看来源、评分上下文、发现轨迹和当前语料的研究版图。','Inspect sources, score contexts, discovery trails and the corpus landscape.')}}</p></article><article class="feature"><span>03 / HANDOFF</span><h3>{{t('判断与交接','Decide and hand off')}}</h3><p>{{t('人工筛选、标定与全文获取各自保留事实，导出可复核的证据包。','Screening, calibration and full-text retrieval preserve distinct facts. Export a reviewable evidence pack.')}}</p></article></div></section>
      <HomeOverview :lang="lang"/>
      <section class="home-band"><div class="row-between"><h2>{{t('最近项目','Recent projects')}}</h2><router-link to="/projects">{{t('查看全部 →','View all →')}}</router-link></div><div v-if="projects.length" class="cols-3"><router-link v-for="p in projects.slice(0,3)" :key="p.project_id" :to="`/projects/${p.project_id}/results`" class="project-card"><h3>{{p.name||p.topic}}</h3><p>{{p.topic}}</p><span>{{p.demo?t('模拟项目','Demo project'):t('研究项目','Research project')}} · {{display(p.updated_at)}}</span></router-link></div><p v-else class="muted">{{t('暂无项目。开始检索或创建隔离演示项目。','No projects yet. Start a search or create an isolated demo.')}}</p></section>
    </template>

</template>
