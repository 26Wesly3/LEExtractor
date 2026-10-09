/* ============================================================================
   LEExtractor — 示例数据层（SYNTHETIC DEMO）
   ----------------------------------------------------------------------------
   这份文件是整个原型的唯一数据真源。分面统计、引文图、PRISMA 账本、候选研究
   问题、导出清单全部由下面的 papers 数组**确定性推导**，不另写死数字，因此
   页与页之间的数字天然自洽。

   字段名与取值严格对齐工作区 LEExtractor v0.9.5 源码：
     Paper                  litsearch/models.py
     PaperIdentifiers       litsearch/identifiers.py
     DiscoveryTrace         litsearch/models.py
     StopReason             litsearch/stop_reasons.py
     HttpBudget.snapshot()  litsearch/stop_reasons.py
     screening_status       litsearch/screening.py  SCREENING_STATUS_VALUES
     关系类型               litsearch/evidence.py    RELATION_TYPES
     候选问题 kind          litsearch/questions.py   _KINDS

   全部内容为合成的界面演示数据，不是真实文献，也不是科研证据。
   ========================================================================== */

'use strict';

(function (global) {
  const VERSION = '0.9.5';
  const DEMO_TAG = 'SYNTHETIC DEMO';
  const PROJECT_ID = 'prj-scspatial-2026a';

  /* 两个评分上下文：跨上下文的分绝不能直接比较，界面必须显式提示。
     见 models.Paper.score_context_id 的说明。 */
  const CTX_MAIN = 'ctx-8f31c2a4';   // 多源合并检索批次
  const CTX_SNOW = 'ctx-1d47b90e';   // 引文扩展新增批次
  const CTX = { main: CTX_MAIN, snowball: CTX_SNOW };

  const ts = (d) => `${d}T09:00:00+00:00`;

  function P(o) {
    return Object.assign({
      abstract: null, authors: [], year: null, venue: null, doi: null,
      citation_count: 0, reference_count: 0, citation_ids: [], reference_ids: [],
      source: '', url: null, relevance_score: 0, topics: [],
      identifiers: { doi: '', semantic_scholar_id: '', openalex_id: '', arxiv_id: '', pmid: '' },
      discovery_traces: [], score_breakdown: {}, score_context_id: CTX_MAIN,
    }, o);
  }

  /* ---------------------------------------------------------------- 语料：30 篇 */
  const papers = [

    /* ---- 空间转录组 / 单细胞 主线（12 篇英文） ---- */
    P({
      id: '10.1038/s41592-024-02138-4', doi: '10.1038/s41592-024-02138-4',
      title: 'Benchmarking spatial transcriptomics deconvolution methods',
      abstract: 'We compare eleven deconvolution methods across simulated and real spatial transcriptomics datasets, reporting that accuracy depends more on reference atlas quality than on the algorithm family.',
      authors: [{ name: 'A. Lindqvist', author_id: 's2:1001' }, { name: 'M. Okafor', author_id: 's2:1002' }],
      year: 2024, venue: 'Nature Methods', source: 'openalex', citation_count: 412, reference_count: 58,
      url: 'https://doi.org/10.1038/s41592-024-02138-4',
      relevance_score: 0.941, topics: ['空间解卷积', '方法基准', '参考图谱'],
      identifiers: { doi: '10.1038/s41592-024-02138-4', openalex_id: 'W4412008801', semantic_scholar_id: '' },
      reference_ids: ['10.1016/j.cels.2023.03.001', '10.1038/s41586-023-06291-4', '10.1186/s13059-023-02910-5', '10.1038/s41576-023-00629-2'],
      score_breakdown: { word: 0.83, char: 0.71, coverage: 0.96 },
      discovery_traces: [{ method: 'systematic_search', provider: 'openalex', query: 'spatial transcriptomics deconvolution benchmark', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: 's2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', doi: '10.1126/science.abp8643',
      title: 'A single-cell reference atlas of the human cortex',
      abstract: 'A curated reference of 3.1 million cells spanning 24 cortical regions, intended as a common coordinate framework for annotation transfer.',
      authors: [{ name: 'R. Venkatesan', author_id: 's2:1103' }, { name: 'L. Fischer', author_id: 's2:1104' }, { name: 'J. Whitfield', author_id: 's2:1105' }],
      year: 2023, venue: 'Science', source: 'semantic_scholar', citation_count: 1876, reference_count: 132,
      url: 'https://www.semanticscholar.org/paper/20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1',
      relevance_score: 0.902, topics: ['参考图谱', '细胞类型注释'],
      identifiers: { doi: '10.1126/science.abp8643', semantic_scholar_id: '20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', openalex_id: 'W4201100333' },
      reference_ids: [],
      score_breakdown: { word: 0.79, char: 0.68, coverage: 0.94 },
      discovery_traces: [{ method: 'systematic_search', provider: 'semantic_scholar', query: 'single cell reference atlas cortex', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1038/s41587-024-02251-x', doi: '10.1038/s41587-024-02251-x',
      title: 'Alignment of single-cell and spatial transcriptomics via optimal transport',
      abstract: 'An optimal-transport formulation that jointly aligns expression and spatial coordinates, with a reported reduction in annotation transfer error on held-out sections.',
      authors: [{ name: 'S. Delacroix', author_id: 's2:1207' }, { name: 'H. Nakamura', author_id: 's2:1208' }],
      year: 2024, venue: 'Nature Biotechnology', source: 'openalex', citation_count: 233, reference_count: 47,
      url: 'https://doi.org/10.1038/s41587-024-02251-x',
      relevance_score: 0.887, topics: ['数据配准', '最优传输', '细胞类型注释'],
      identifiers: { doi: '10.1038/s41587-024-02251-x', openalex_id: 'W4419812205' },
      reference_ids: ['s2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', '10.1038/s41592-024-02138-4', '10.1093/bioinformatics/btae102'],
      score_breakdown: { word: 0.81, char: 0.66, coverage: 0.91 },
      discovery_traces: [{ method: 'systematic_search', provider: 'openalex', query: 'single cell spatial alignment optimal transport', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1186/s13059-023-02910-5', doi: '10.1186/s13059-023-02910-5',
      title: 'Spatially resolved cell type mapping with probabilistic models',
      abstract: 'A hierarchical Bayesian model that propagates annotation uncertainty into spatial maps instead of returning point estimates only.',
      authors: [{ name: 'P. Almeida', author_id: 's2:1309' }],
      year: 2023, venue: 'Genome Biology', source: 'crossref', citation_count: 158, reference_count: 39,
      url: 'https://doi.org/10.1186/s13059-023-02910-5',
      relevance_score: 0.831, topics: ['细胞类型注释', '概率模型'],
      identifiers: { doi: '10.1186/s13059-023-02910-5', openalex_id: 'W4388011224' },
      reference_ids: ['s2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', '10.1038/s41592-025-02204-9'],
      score_breakdown: { word: 0.74, char: 0.61, coverage: 0.88 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: 'spatial cell type mapping probabilistic', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1038/s41592-025-02204-9', doi: '10.1038/s41592-025-02204-9',
      title: 'Cell segmentation-free spatial neighbourhood analysis',
      abstract: 'Neighbourhood enrichment computed directly on spatial spots, avoiding segmentation error propagation; validated against matched imaging-based data.',
      authors: [{ name: 'T. Bergström', author_id: 's2:1410' }, { name: 'Y. Chen', author_id: 's2:1411' }],
      year: 2025, venue: 'Nature Methods', source: 'openalex', citation_count: 61, reference_count: 33,
      url: 'https://doi.org/10.1038/s41592-025-02204-9',
      relevance_score: 0.812, topics: ['空间邻域', '细胞互作'],
      identifiers: { doi: '10.1038/s41592-025-02204-9', openalex_id: 'W4426671903' },
      reference_ids: ['10.1186/s13059-023-02910-5', '10.1038/s41592-024-02138-4'],
      score_breakdown: { word: 0.72, char: 0.59, coverage: 0.86 },
      discovery_traces: [{ method: 'systematic_search', provider: 'openalex', query: 'spatial neighbourhood enrichment segmentation free', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1093/bioinformatics/btae102', doi: '10.1093/bioinformatics/btae102',
      title: 'Graph neural networks for spatial domain identification',
      abstract: 'Benchmarks six graph constructions for spatial domain detection and shows that graph topology choice dominates hyperparameter tuning.',
      authors: [{ name: 'N. Iyer', author_id: 's2:1512' }],
      year: 2024, venue: 'Bioinformatics', source: 'crossref', citation_count: 97, reference_count: 41,
      url: 'https://doi.org/10.1093/bioinformatics/btae102',
      relevance_score: 0.776, topics: ['空间域识别', '图神经网络'],
      identifiers: { doi: '10.1093/bioinformatics/btae102', openalex_id: 'W4405560012' },
      reference_ids: ['10.1038/s41592-024-02138-4', '10.1186/s13059-023-02910-5'],
      score_breakdown: { word: 0.7, char: 0.55, coverage: 0.82 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: 'graph neural network spatial domain', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1038/s41467-024-48120-7', doi: '10.1038/s41467-024-48120-7',
      title: 'Evaluating ligand-receptor inference from spatial data',
      abstract: 'Systematic evaluation showing that most ligand-receptor tools are sensitive to neighbourhood radius, with limited agreement between methods on the same section.',
      authors: [{ name: 'C. Moreau', author_id: 's2:1613' }, { name: 'D. Krishnan', author_id: 's2:1614' }],
      year: 2024, venue: 'Nature Communications', source: 'semantic_scholar', citation_count: 142, reference_count: 52,
      url: 'https://doi.org/10.1038/s41467-024-48120-7',
      relevance_score: 0.754, topics: ['细胞互作', '配体受体', '方法基准'],
      identifiers: { doi: '10.1038/s41467-024-48120-7', semantic_scholar_id: '7c1a9e4b2d8f3a6c0e5b7d9f1a3c5e7b9d0f2a4c', openalex_id: 'W4402288110' },
      reference_ids: ['s2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', '10.1038/s41592-025-02204-9'],
      score_breakdown: { word: 0.69, char: 0.52, coverage: 0.8 },
      discovery_traces: [{ method: 'systematic_search', provider: 'semantic_scholar', query: 'ligand receptor spatial inference evaluation', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1038/s41592-022-01492-5', doi: '10.1038/s41592-022-01492-5',
      title: 'A benchmark of batch integration for single-cell data',
      abstract: 'Benchmark of fourteen integration methods across 32 tasks, reporting that no method dominates on both batch removal and biological conservation.',
      authors: [{ name: 'F. Lambert', author_id: 's2:1715' }],
      year: 2022, venue: 'Nature Methods', source: 'semantic_scholar', citation_count: 903, reference_count: 88,
      url: 'https://doi.org/10.1038/s41592-022-01492-5',
      relevance_score: 0.703, topics: ['方法基准', '批次整合'],
      identifiers: { doi: '10.1038/s41592-022-01492-5', semantic_scholar_id: '3a5c7e9b1d4f6a8c0e2b4d6f8a0c2e4b6d8f0a2c', openalex_id: 'W4290011445' },
      reference_ids: [],
      score_breakdown: { word: 0.66, char: 0.5, coverage: 0.79 },
      discovery_traces: [{ method: 'systematic_search', provider: 'semantic_scholar', query: 'batch integration benchmark single cell', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1016/j.cels.2023.03.001', doi: '10.1016/j.cels.2023.03.001',
      title: 'Reference-free deconvolution of spatial spots',
      abstract: 'Estimates cell type composition without an external reference by exploiting within-slide variability, at the cost of interpretability of the recovered factors.',
      authors: [{ name: 'K. Tanaka', author_id: 's2:1816' }, { name: 'E. Novak', author_id: 's2:1817' }],
      year: 2023, venue: 'Cell Systems', source: 'openalex', citation_count: 205, reference_count: 44,
      url: 'https://doi.org/10.1016/j.cels.2023.03.001',
      relevance_score: 0.688, topics: ['空间解卷积', '参考图谱'],
      identifiers: { doi: '10.1016/j.cels.2023.03.001', openalex_id: 'W4327781122' },
      reference_ids: ['s2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', '10.1038/s41592-024-02138-4'],
      score_breakdown: { word: 0.65, char: 0.49, coverage: 0.77 },
      discovery_traces: [{ method: 'systematic_search', provider: 'openalex', query: 'reference free deconvolution spatial', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1038/s41586-025-08841-2', doi: '10.1038/s41586-025-08841-2',
      title: 'Multi-modal integration of histology and transcriptomics',
      abstract: 'Joint embedding of H&E images and spatial expression, with an ablation showing image features carry most of the domain signal on the tested tissues.',
      authors: [{ name: 'I. Petrova', author_id: 's2:1918' }, { name: 'W. Osei', author_id: 's2:1919' }],
      year: 2025, venue: 'Nature', source: 'openalex', citation_count: 47, reference_count: 36,
      url: 'https://doi.org/10.1038/s41586-025-08841-2',
      relevance_score: 0.664, topics: ['多模态整合', '空间域识别'],
      identifiers: { doi: '10.1038/s41586-025-08841-2', openalex_id: 'W4429980017' },
      reference_ids: ['10.1038/s41587-024-02251-x', '10.1038/s41592-025-02204-9', '10.1038/s41467-024-48120-7'],
      score_breakdown: { word: 0.63, char: 0.47, coverage: 0.75 },
      discovery_traces: [{ method: 'systematic_search', provider: 'openalex', query: 'histology transcriptomics joint embedding', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: 'arxiv:2503.04412', doi: null,
      title: 'Uncertainty quantification in cell type deconvolution',
      abstract: 'Preprint proposing conformal prediction intervals for spot-level composition estimates; no external validation cohort yet.',
      authors: [{ name: 'J. Sørensen', author_id: 's2:2020' }],
      year: 2025, venue: 'arXiv (preprint)', source: 'arxiv', citation_count: 12, reference_count: 28,
      url: 'https://arxiv.org/abs/2503.04412',
      relevance_score: 0.641, topics: ['空间解卷积', '不确定性量化'],
      identifiers: { doi: '', arxiv_id: '2503.04412', openalex_id: 'W4431200884' },
      reference_ids: ['10.1038/s41592-024-02138-4', '10.1016/j.cels.2023.03.001'],
      score_breakdown: { word: 0.61, char: 0.45, coverage: 0.73 },
      discovery_traces: [{ method: 'systematic_search', provider: 'arxiv', query: 'deconvolution uncertainty quantification', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1016/j.ccell.2024.05.011', doi: '10.1016/j.ccell.2024.05.011',
      title: 'Spatial transcriptomics in tumour microenvironment studies',
      abstract: 'Review of spatial profiling in solid tumours, with a table of cohort sizes and a candid discussion of reproducibility across platforms.',
      authors: [{ name: 'M. Haddad', author_id: 's2:2121' }, { name: 'R. Lindgren', author_id: 's2:2122' }],
      year: 2024, venue: 'Cancer Cell', source: 'crossref', citation_count: 311, reference_count: 176,
      url: 'https://doi.org/10.1016/j.ccell.2024.05.011',
      relevance_score: 0.622, topics: ['肿瘤微环境', '空间邻域'],
      identifiers: { doi: '10.1016/j.ccell.2024.05.011', openalex_id: 'W4403391221' },
      reference_ids: ['s2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', '10.1038/s41467-024-48120-7', '10.1038/s41576-023-00629-2'],
      score_breakdown: { word: 0.6, char: 0.44, coverage: 0.71 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: 'tumour microenvironment spatial transcriptomics', timestamp: ts('2026-10-08') }],
    }),

    /* ---- 中文文献（6 篇，中英混排） ---- */
    P({
      id: '10.13345/j.cjb.240312', doi: '10.13345/j.cjb.240312',
      title: '单细胞与空间转录组联合分析中的配准误差评估',
      abstract: '本文系统评估了六种配准策略在组织切片形变条件下的误差来源，指出切片形变校正的缺失是跨模态注释迁移失败的主要原因。',
      authors: [{ name: '李明远' }, { name: '赵一鸣' }],
      year: 2024, venue: '生物工程学报', source: 'crossref', citation_count: 34, reference_count: 29,
      url: 'https://doi.org/10.13345/j.cjb.240312',
      relevance_score: 0.918, topics: ['数据配准', '细胞类型注释'],
      identifiers: { doi: '10.13345/j.cjb.240312', openalex_id: 'W4406671002' },
      reference_ids: ['10.1038/s41587-024-02251-x', '10.1038/s41592-024-02138-4'],
      score_breakdown: { word: 0.8, char: 0.69, coverage: 0.93 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: '单细胞 空间转录组 配准 误差', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.16288/j.yczz.24-118', doi: '10.16288/j.yczz.24-118',
      title: '空间转录组细胞类型解卷积方法比较研究',
      abstract: '在四套公开数据集上比较了九种解卷积工具，发现参考图谱与目标组织的组织来源一致性比算法选择影响更大。',
      authors: [{ name: '王思远' }, { name: '陈静' }],
      year: 2024, venue: '遗传', source: 'crossref', citation_count: 28, reference_count: 34,
      url: 'https://doi.org/10.16288/j.yczz.24-118',
      relevance_score: 0.874, topics: ['空间解卷积', '方法基准'],
      identifiers: { doi: '10.16288/j.yczz.24-118', openalex_id: 'W4407781233' },
      reference_ids: ['10.1038/s41592-024-02138-4', '10.1016/j.cels.2023.03.001'],
      score_breakdown: { word: 0.76, char: 0.64, coverage: 0.9 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: '空间转录组 解卷积 比较', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.11897/SP.J.1016.2025.01120', doi: '10.11897/SP.J.1016.2025.01120',
      title: '基于图神经网络的空间域识别及其可解释性',
      abstract: '提出在图卷积中加入空间邻接先验的变体，并给出基于扰动的可解释性评估；在三个数据集上相对基线提升有限但稳定性更好。',
      authors: [{ name: '刘思齐' }],
      year: 2025, venue: '计算机学报', source: 'crossref', citation_count: 19, reference_count: 26,
      url: 'https://doi.org/10.11897/SP.J.1016.2025.01120',
      relevance_score: 0.842, topics: ['空间域识别', '图神经网络'],
      identifiers: { doi: '10.11897/SP.J.1016.2025.01120', openalex_id: 'W4428810045' },
      reference_ids: ['10.1093/bioinformatics/btae102', '10.1186/s13059-023-02910-5'],
      score_breakdown: { word: 0.73, char: 0.6, coverage: 0.87 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: '图神经网络 空间域 识别', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1360/SSV-2025-0043', doi: '10.1360/SSV-2025-0043',
      title: '肿瘤微环境空间邻域分析的统计框架',
      abstract: '建立了一套邻域富集的零模型与多重比较校正流程，指出未校正的邻域富集结果在多数已发表分析中被高估。',
      authors: [{ name: '孙雅' }, { name: '何嘉宁' }],
      year: 2025, venue: '中国科学：生命科学', source: 'crossref', citation_count: 15, reference_count: 31,
      url: 'https://doi.org/10.1360/SSV-2025-0043',
      relevance_score: 0.808, topics: ['空间邻域', '统计框架', '肿瘤微环境'],
      identifiers: { doi: '10.1360/SSV-2025-0043', openalex_id: 'W4430021177' },
      reference_ids: ['10.1038/s41592-025-02204-9', '10.1016/j.ccell.2024.05.011'],
      score_breakdown: { word: 0.7, char: 0.57, coverage: 0.85 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: '肿瘤微环境 空间邻域 统计', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.16476/j.pibb.2025.0087', doi: '10.16476/j.pibb.2025.0087',
      title: '单细胞参考图谱跨组织迁移的泛化性研究',
      abstract: '评估了在一种组织上训练的注释模型迁移到另一种组织时的性能衰减，给出按组织分层评估的建议。',
      authors: [{ name: '周文博' }],
      year: 2025, venue: '生物化学与生物物理进展', source: 'crossref', citation_count: 11, reference_count: 23,
      url: 'https://doi.org/10.16476/j.pibb.2025.0087',
      relevance_score: 0.771, topics: ['参考图谱', '泛化性'],
      identifiers: { doi: '10.16476/j.pibb.2025.0087', openalex_id: 'W4431190066' },
      reference_ids: ['s2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', '10.1038/s41592-022-01492-5'],
      score_breakdown: { word: 0.67, char: 0.54, coverage: 0.82 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: '参考图谱 迁移 泛化', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.13345/j.cjb.230588', doi: '10.13345/j.cjb.230588',
      title: '空间多组学数据整合的质量控制指标',
      abstract: '汇总了空间多组学实验中应报告的质控指标清单，并指出当前文献中缺失率较高的三项。',
      authors: [{ name: '郑亦凡' }, { name: '林小雨' }],
      year: 2023, venue: '生物工程学报', source: 'crossref', citation_count: 41, reference_count: 37,
      url: 'https://doi.org/10.13345/j.cjb.230588',
      relevance_score: 0.735, topics: ['多模态整合', '质量控制', '可复现性'],
      identifiers: { doi: '10.13345/j.cjb.230588', openalex_id: 'W4389901122' },
      reference_ids: ['10.1038/s41586-025-08841-2', '10.1038/s41587-024-02251-x'],
      score_breakdown: { word: 0.64, char: 0.51, coverage: 0.8 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: '空间多组学 质控 指标', timestamp: ts('2026-10-08') }],
    }),

    /* ---- 具身智能（6 篇，引文扩展新增，属于另一个评分上下文） ---- */
    P({
      id: 'arxiv:2502.08871', doi: null,
      title: 'Vision-language-action models for robotic manipulation',
      abstract: 'Preprint surveying VLA architectures and reporting that evaluation protocols differ so much between papers that cross-study comparison is currently unreliable.',
      authors: [{ name: 'H. Zhaoming' }, { name: 'P. Novak' }],
      year: 2025, venue: 'arXiv (preprint)', source: 'arxiv', citation_count: 156, reference_count: 91,
      url: 'https://arxiv.org/abs/2502.08871',
      relevance_score: 0.412, topics: ['视觉-语言-动作', '泛化性'],
      identifiers: { doi: '', arxiv_id: '2502.08871', openalex_id: 'W4428900112' },
      reference_ids: [], score_context_id: CTX_SNOW,
      score_breakdown: { word: 0.44, char: 0.31, coverage: 0.48 },
      discovery_traces: [{ method: 'snowball_forward', provider: 'arxiv', query: 'VLA robotic manipulation', seed_id: 'arxiv:2502.08871', round_no: 1, score: 0.44, evidence_ids: ['arxiv:2502.08871'], timestamp: ts('2026-10-08') }],
    }),
    P({
      id: 's2:9b2e5d7f1a3c5e7b9d0f2a4c6e8b0d2f4a6c8e0b', doi: '10.15607/RSS.2024.XIX.041',
      title: 'Diffusion policies for dexterous manipulation',
      abstract: 'Trains a diffusion policy on 1.2k demonstrations and reports success rates on six dexterous tasks with an ablation over action horizon.',
      authors: [{ name: 'L. Moretti' }, { name: 'S. Aoki' }],
      year: 2024, venue: 'Robotics: Science and Systems', source: 'semantic_scholar', citation_count: 512, reference_count: 63,
      url: 'https://doi.org/10.15607/RSS.2024.XIX.041',
      relevance_score: 0.388, topics: ['扩散策略', '模仿学习'],
      identifiers: { doi: '10.15607/RSS.2024.XIX.041', semantic_scholar_id: '9b2e5d7f1a3c5e7b9d0f2a4c6e8b0d2f4a6c8e0b', openalex_id: 'W4407781990' },
      reference_ids: ['arxiv:2502.08871'], score_context_id: CTX_SNOW,
      score_breakdown: { word: 0.41, char: 0.29, coverage: 0.45 },
      discovery_traces: [{ method: 'snowball_forward', provider: 'semantic_scholar', query: 'dexterous manipulation policy', seed_id: 'arxiv:2502.08871', round_no: 1, score: 0.41, evidence_ids: ['arxiv:2502.08871'], timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.15607/CoRL.2025.021', doi: '10.15607/CoRL.2025.021',
      title: 'Cross-embodiment transfer in robot foundation models',
      abstract: 'Studies transfer between robot platforms and finds that action-space normalisation matters more than dataset scale in the tested regime.',
      authors: [{ name: 'V. Ramaswamy' }],
      year: 2025, venue: 'Conference on Robot Learning', source: 'openalex', citation_count: 73, reference_count: 48,
      url: 'https://doi.org/10.15607/CoRL.2025.021',
      relevance_score: 0.361, topics: ['跨本体迁移', '基础模型'],
      identifiers: { doi: '10.15607/CoRL.2025.021', openalex_id: 'W4432110044' },
      reference_ids: ['arxiv:2502.08871', 's2:9b2e5d7f1a3c5e7b9d0f2a4c6e8b0d2f4a6c8e0b'], score_context_id: CTX_SNOW,
      score_breakdown: { word: 0.38, char: 0.26, coverage: 0.42 },
      discovery_traces: [{ method: 'snowball_forward', provider: 'openalex', query: 'cross embodiment robot foundation model', seed_id: 'arxiv:2502.08871', round_no: 1, score: 0.38, evidence_ids: ['arxiv:2502.08871'], timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1109/ICRA.2024.10611234', doi: '10.1109/ICRA.2024.10611234',
      title: 'Imitation learning from heterogeneous demonstrations',
      abstract: 'Mixes demonstrations collected under different teleoperation interfaces and quantifies the cost of not modelling the interface as a confounder.',
      authors: [{ name: 'B. Fischer' }, { name: 'Q. Zhang' }],
      year: 2024, venue: 'IEEE ICRA', source: 'semantic_scholar', citation_count: 188, reference_count: 55,
      url: 'https://doi.org/10.1109/ICRA.2024.10611234',
      relevance_score: 0.334, topics: ['模仿学习', '数据异质性'],
      identifiers: { doi: '10.1109/ICRA.2024.10611234', semantic_scholar_id: '1f3d5b7a9c2e4d6f8a0b2c4d6e8f0a2b4c6d8e0f', openalex_id: 'W4401122778' },
      reference_ids: ['s2:9b2e5d7f1a3c5e7b9d0f2a4c6e8b0d2f4a6c8e0b'], score_context_id: CTX_SNOW,
      score_breakdown: { word: 0.35, char: 0.24, coverage: 0.4 },
      discovery_traces: [{ method: 'snowball_forward', provider: 'semantic_scholar', query: 'imitation learning heterogeneous demonstrations', seed_id: 'arxiv:2502.08871', round_no: 2, score: 0.35, evidence_ids: ['arxiv:2502.08871'], timestamp: ts('2026-10-08') }],
    }),
    P({
      id: 'arxiv:2506.02117', doi: null,
      title: 'Benchmarking generalisation in robot manipulation',
      abstract: 'Proposes a held-out task suite and reports that most reported gains vanish when the test scenes are drawn from a different distribution.',
      authors: [{ name: 'A. Kowalski' }],
      year: 2025, venue: 'arXiv (preprint)', source: 'arxiv', citation_count: 44, reference_count: 39,
      url: 'https://arxiv.org/abs/2506.02117',
      relevance_score: 0.309, topics: ['泛化性', '方法基准'],
      identifiers: { doi: '', arxiv_id: '2506.02117', openalex_id: 'W4433300221' },
      reference_ids: ['arxiv:2502.08871', '10.15607/CoRL.2025.021'], score_context_id: CTX_SNOW,
      score_breakdown: { word: 0.33, char: 0.22, coverage: 0.37 },
      discovery_traces: [{ method: 'snowball_forward', provider: 'arxiv', query: 'generalisation benchmark manipulation', seed_id: 'arxiv:2502.08871', round_no: 2, score: 0.33, evidence_ids: ['arxiv:2502.08871'], timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.13973/j.cnki.robot.250033', doi: '10.13973/j.cnki.robot.250033',
      title: '触觉反馈与视觉-语言-动作模型的融合',
      abstract: '在 VLA 策略中引入触觉通道，报告在接触丰富任务上的成功率提升，但未报告未见任务上的表现。',
      authors: [{ name: '吴天成' }],
      year: 2025, venue: '机器人', source: 'crossref', citation_count: 9, reference_count: 21,
      url: 'https://doi.org/10.13973/j.cnki.robot.250033',
      relevance_score: 0.288, topics: ['触觉反馈', '视觉-语言-动作'],
      identifiers: { doi: '10.13973/j.cnki.robot.250033', openalex_id: 'W4434400889' },
      reference_ids: ['arxiv:2502.08871', '10.1109/ICRA.2024.10611234'], score_context_id: CTX_SNOW,
      score_breakdown: { word: 0.31, char: 0.21, coverage: 0.35 },
      discovery_traces: [{ method: 'snowball_forward', provider: 'crossref', query: '触觉 视觉语言动作 融合', seed_id: 'arxiv:2502.08871', round_no: 2, score: 0.31, evidence_ids: ['arxiv:2502.08871'], timestamp: ts('2026-10-08') }],
    }),

    /* ---- 综述与可复现性（6 篇） ---- */
    P({
      id: '10.1038/s41576-023-00629-2', doi: '10.1038/s41576-023-00629-2',
      title: 'A survey of spatial transcriptomics computational methods',
      abstract: 'Survey covering alignment, deconvolution, domain detection and neighbourhood analysis, with a comparison table of assumptions per tool.',
      authors: [{ name: 'G. Andersson' }, { name: 'T. Mbeki' }],
      year: 2023, venue: 'Nature Reviews Genetics', source: 'openalex', citation_count: 721, reference_count: 214,
      url: 'https://doi.org/10.1038/s41576-023-00629-2',
      relevance_score: 0.902, topics: ['综述', '方法基准'],
      identifiers: { doi: '10.1038/s41576-023-00629-2', openalex_id: 'W4366671100' },
      reference_ids: ['s2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', '10.1038/s41592-024-02138-4', '10.1038/s41592-022-01492-5'],
      score_breakdown: { word: 0.81, char: 0.7, coverage: 0.95 },
      discovery_traces: [{ method: 'systematic_search', provider: 'openalex', query: 'spatial transcriptomics computational methods survey', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1186/s13059-025-03011-9', doi: '10.1186/s13059-025-03011-9',
      title: 'Open problems in spatial omics benchmarking',
      abstract: 'Position paper arguing that current benchmarks over-represent simulated data and under-report preprocessing choices.',
      authors: [{ name: 'S. Rahman' }],
      year: 2025, venue: 'Genome Biology', source: 'crossref', citation_count: 26, reference_count: 44,
      url: 'https://doi.org/10.1186/s13059-025-03011-9',
      relevance_score: 0.866, topics: ['方法基准', '可复现性'],
      identifiers: { doi: '10.1186/s13059-025-03011-9', openalex_id: 'W4429011556' },
      reference_ids: ['10.1038/s41592-024-02138-4', '10.1038/s41592-022-01492-5', '10.1038/s41586-025-08841-2'],
      score_breakdown: { word: 0.77, char: 0.63, coverage: 0.9 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: 'spatial omics benchmarking open problems', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1016/j.cell.2024.06.021', doi: '10.1016/j.cell.2024.06.021',
      title: 'Foundation models for single-cell biology',
      abstract: 'Reviews single-cell foundation models and their evaluation, noting that downstream benchmarks rarely include batch-shifted data.',
      authors: [{ name: 'Y. Haruka' }, { name: 'N. Brandt' }],
      year: 2024, venue: 'Cell', source: 'openalex', citation_count: 264, reference_count: 158,
      url: 'https://doi.org/10.1016/j.cell.2024.06.021',
      relevance_score: 0.824, topics: ['基础模型', '方法基准'],
      identifiers: { doi: '10.1016/j.cell.2024.06.021', openalex_id: 'W4404455112' },
      reference_ids: ['s2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', '10.1038/s41592-022-01492-5'],
      score_breakdown: { word: 0.75, char: 0.58, coverage: 0.88 },
      discovery_traces: [{ method: 'systematic_search', provider: 'openalex', query: 'foundation model single cell evaluation', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1038/s41592-023-01902-3', doi: '10.1038/s41592-023-01902-3',
      title: 'Spatial transcriptomics protocol comparison across platforms',
      abstract: 'Side-by-side comparison of five platforms on serial sections of the same tissue, reporting capture efficiency and spot size trade-offs.',
      authors: [{ name: 'E. Lindberg' }, { name: 'A. Costa' }],
      year: 2023, venue: 'Nature Methods', source: 'semantic_scholar', citation_count: 340, reference_count: 67,
      url: 'https://doi.org/10.1038/s41592-023-01902-3',
      relevance_score: 0.792, topics: ['平台比较', '方法基准'],
      identifiers: { doi: '10.1038/s41592-023-01902-3', semantic_scholar_id: '5c7e9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f9a1c3e', openalex_id: 'W4360011223' },
      reference_ids: ['10.1038/s41576-023-00629-2'],
      score_breakdown: { word: 0.71, char: 0.55, coverage: 0.85 },
      discovery_traces: [{ method: 'systematic_search', provider: 'semantic_scholar', query: 'spatial transcriptomics platform comparison', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1093/bioinformatics/btaf118', doi: '10.1093/bioinformatics/btaf118',
      title: 'Statistical power in spatial transcriptomics experiments',
      abstract: 'Derives power curves for domain detection as a function of section count and spot density, and gives a minimum-reporting checklist.',
      authors: [{ name: 'O. Berg' }],
      year: 2025, venue: 'Bioinformatics', source: 'crossref', citation_count: 18, reference_count: 30,
      url: 'https://doi.org/10.1093/bioinformatics/btaf118',
      relevance_score: 0.752, topics: ['统计框架', '实验设计'],
      identifiers: { doi: '10.1093/bioinformatics/btaf118', openalex_id: 'W4430055119' },
      reference_ids: ['10.1038/s41592-023-01902-3', '10.1093/bioinformatics/btae102'],
      score_breakdown: { word: 0.68, char: 0.52, coverage: 0.83 },
      discovery_traces: [{ method: 'systematic_search', provider: 'crossref', query: 'statistical power spatial transcriptomics', timestamp: ts('2026-10-08') }],
    }),
    P({
      id: '10.1038/s41467-025-61234-8', doi: '10.1038/s41467-025-61234-8',
      title: 'Reproducibility of spatial domain detection across studies',
      abstract: 'Reanalyses eleven published datasets and finds that domain labels agree only moderately between methods, with preprocessing as the largest single source of divergence.',
      authors: [{ name: 'K. Watanabe' }, { name: 'F. Dube' }],
      year: 2025, venue: 'Nature Communications', source: 'openalex', citation_count: 22, reference_count: 51,
      url: 'https://doi.org/10.1038/s41467-025-61234-8',
      relevance_score: 0.716, topics: ['可复现性', '空间域识别'],
      identifiers: { doi: '10.1038/s41467-025-61234-8', openalex_id: 'W4431188223' },
      reference_ids: ['10.1093/bioinformatics/btae102', '10.1038/s41576-023-00629-2', '10.1038/s41592-023-01902-3'],
      score_breakdown: { word: 0.65, char: 0.5, coverage: 0.81 },
      discovery_traces: [{ method: 'systematic_search', provider: 'openalex', query: 'reproducibility spatial domain detection', timestamp: ts('2026-10-08') }],
    }),
  ];

  /* ---------------------------------------------------------------- 被去重掉的重复记录
     三个库返回的原始命中里，有 3 条与主列表是同一篇（同一 DOI 的不同来源表示）。
     它们**不算**在当前列表里，但计入 raw_count / provider_counts —— 界面必须
     把「数据源返回」与「去重后」分开，绝不能相加当成文献数。 */
  const duplicates = [
    { mergedInto: '10.1038/s41592-024-02138-4', provider: 'semantic_scholar', providerId: 's2:aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00',
      matchedBy: 'doi', note: '同一 DOI 由 Semantic Scholar 再次返回' },
    { mergedInto: 's2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1', provider: 'openalex', providerId: 'openalex:W4201100333',
      matchedBy: 'openalex_id', note: 'DOI 与 OpenAlex ID 双命中' },
    { mergedInto: '10.1038/s41592-022-01492-5', provider: 'crossref', providerId: 'crossref:10.1038/s41592-022-01492-5',
      matchedBy: 'title_year_fallback', note: 'Crossref 未给 DOI 大小写一致形式，按标题+年份兜底合并' },
  ];

  /* ---------------------------------------------------------------- 检索清单 */
  const manifest = {
    mode: 'systematic_search',
    fixture_version: 1,
    query: '单细胞与空间转录组的联合分析',
    research_direction: '用空间转录组验证单细胞注释在组织切片上的可靠性，并量化配准误差对结论的影响',
    // 漏斗四步：数据源返回 → 去重后 → 排序后保留 → 当前列表
    raw_count: 347,
    unique_before_filters: 289,
    returned_count: 30,
    provider_counts: { semantic_scholar: 132, openalex: 118, crossref: 71, arxiv: 26 },
    warning: '全部为合成的界面演示记录，不是真实文献，也不能作为科研证据。',
    research_intent: {
      topic_terms: ['single-cell', 'spatial transcriptomics', 'integration', 'deconvolution'],
      direction_terms: ['annotation transfer', 'registration error', 'reproducibility'],
      year_from: 2021,
      year_to: 2025,
      degraded: false,
      degraded_note: '',
      per_source: [
        { source: 'semantic_scholar', label: 'Semantic Scholar', query: '(single-cell OR scRNA-seq) AND (spatial transcriptomics) AND (integration OR alignment)',
          filter: 'year:2021-2025, fieldsOfStudy:Biology|Medicine', date_range: '2021-2025',
          note: '支持字段过滤，返回量与速率受限' },
        { source: 'openalex', label: 'OpenAlex', query: 'single-cell spatial transcriptomics integration deconvolution',
          filter: 'from_publication_date:2021-01-01, type:article', date_range: '2021-2025',
          note: '无年份字段过滤时的降级：仅在本地按年份过滤' },
        { source: 'crossref', label: 'Crossref', query: '单细胞 空间转录组 联合分析 配准',
          filter: '', date_range: '2021-2025',
          note: '中文检索式单独生成；Crossref 不支持相关度排序' },
        { source: 'arxiv', label: 'arXiv', query: 'all:"spatial transcriptomics" AND all:"single cell"',
          filter: 'cat:q-bio.GN OR cat:cs.LG', date_range: '2021-2025',
          note: '预印本，未经同行评审；界面必须区分' },
      ],
      degraded_sources: ['crossref'],
      degradation_note: 'Crossref 不接受相关度排序参数，该库结果按发表年份倒序并入，因此在混合排序中不代表同等相关度。',
    },
  };

  /* ---------------------------------------------------------------- 引文扩展 */
  const snowball = {
    seeds: ['10.1038/s41592-024-02138-4', 's2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1'],
    direction: 'forward',
    rounds_requested: 3,
    rounds_completed: 2,
    stopped_reason: 'max_rounds',
    budget_calls: 240,
    budget_used: 187,
    budget_unit: 'source_calls',
    similar_methods: ['bibliographic_coupling', 'co_citation'],
    rounds: [
      { round_no: 1, raw: 96, unique: 74, relevant: 21, cumulative_unique: 74, cumulative_relevant: 21,
        new_ratio: 0.77, dup_ratio: 0.23, topic_delta: ['视觉-语言-动作', '扩散策略', '跨本体迁移'],
        stopped_reason: '', elapsed_seconds: 42.6 },
      { round_no: 2, raw: 88, unique: 61, relevant: 9, cumulative_unique: 135, cumulative_relevant: 30,
        new_ratio: 0.69, dup_ratio: 0.31, topic_delta: ['泛化性', '触觉反馈'],
        stopped_reason: '', elapsed_seconds: 38.1 },
      { round_no: 3, raw: 0, unique: 0, relevant: 0, cumulative_unique: 135, cumulative_relevant: 30,
        new_ratio: 0, dup_ratio: 0, topic_delta: [], stopped_reason: 'max_rounds', elapsed_seconds: 0, not_run: true },
    ],
  };

  /* ---------------------------------------------------------------- HTTP 预算（真实计数，不是调用次数） */
  const http_budget = {
    requests: 187, retries: 9, cache_hits: 64, rate_limited: 3, errors: 2,
    canceled: false, elapsed_seconds: 118.4,
    by_source: {
      crossref: { requests: 34, retries: 1, rate_limited: 0, errors: 0 },
      openalex: { requests: 58, retries: 3, rate_limited: 1, errors: 1 },
      semantic_scholar: { requests: 71, retries: 5, rate_limited: 2, errors: 1 },
      arxiv: { requests: 24, retries: 0, rate_limited: 0, errors: 0 },
    },
  };

  /* ---------------------------------------------------------------- 筛选与标定 */
  const screening = {
    status: 'preliminary_included',
    threshold_calibrated: false,
    requires_manual_review: true,
    full_text_review_completed: false,
    auto_screen: false,
    calibration: {
      status: 'insufficient_labels',
      labelled_relevant: 6, labelled_irrelevant: 0, threshold: null,
      note: '只有单一类别的标注（全部为相关），不能据此形成二分类阈值；分数仅用于排序。',
      created_at: ts('2026-10-08'),
    },
    stage: 'title_abstract',
    decided: 24, total: 30,
    include: 11, exclude: 9, maybe: 4, undecided: 6,
    conflicts: 1,
  };

  /* 每篇的筛选决定（只给已决定的记录填） */
  const decisions = {
    '10.1038/s41592-024-02138-4': { stage: 'title_abstract', decision: 'include', reason: '直接比较解卷积方法，与配准误差问题正面相关', by: '人工', at: ts('2026-10-08') },
    '10.13345/j.cjb.240312': { stage: 'title_abstract', decision: 'include', reason: '中文文献，配准误差评估，与研究方向一致', by: '人工', at: ts('2026-10-08') },
    '10.16288/j.yczz.24-118': { stage: 'title_abstract', decision: 'include', reason: '解卷积方法比较，可纳入方法学证据', by: '人工', at: ts('2026-10-08') },
    '10.1038/s41587-024-02251-x': { stage: 'title_abstract', decision: 'include', reason: '最优传输配准，直接回答可靠性问题', by: '人工', at: ts('2026-10-08') },
    '10.11897/SP.J.1016.2025.01120': { stage: 'title_abstract', decision: 'include', reason: '空间域识别方法，含可解释性评估', by: '人工', at: ts('2026-10-08') },
    '10.1360/SSV-2025-0043': { stage: 'title_abstract', decision: 'include', reason: '统计框架，可用于讨论多重比较问题', by: '人工', at: ts('2026-10-08') },
    '10.16476/j.pibb.2025.0087': { stage: 'title_abstract', decision: 'include', reason: '跨组织迁移泛化性，直接支持研究方向', by: '人工', at: ts('2026-10-08') },
    '10.1038/s41576-023-00629-2': { stage: 'title_abstract', decision: 'include', reason: '综述，用于定位方法学谱系', by: '人工', at: ts('2026-10-08') },
    '10.1186/s13059-025-03011-9': { stage: 'title_abstract', decision: 'include', reason: '基准评测的开放问题，与可复现性相关', by: '人工', at: ts('2026-10-08') },
    '10.1038/s41467-025-61234-8': { stage: 'title_abstract', decision: 'include', reason: '跨研究可复现性再分析', by: '人工', at: ts('2026-10-08') },
    '10.1093/bioinformatics/btaf118': { stage: 'title_abstract', decision: 'include', reason: '统计功效与最小报告清单', by: '人工', at: ts('2026-10-08') },

    'arxiv:2502.08871': { stage: 'title_abstract', decision: 'maybe', reason: '预印本，且属于另一主题（具身智能），待确认是否属于本次范围', by: '人工', at: ts('2026-10-08') },
    's2:9b2e5d7f1a3c5e7b9d0f2a4c6e8b0d2f4a6c8e0b': { stage: 'title_abstract', decision: 'maybe', reason: '引文扩展命中的相邻领域，需判断相关性', by: '人工', at: ts('2026-10-08') },
    '10.15607/CoRL.2025.021': { stage: 'title_abstract', decision: 'maybe', reason: '跨本体迁移方法与本研究问题结构相似，方法可借用但领域不同', by: '人工', at: ts('2026-10-08') },
    '10.13973/j.cnki.robot.250033': { stage: 'title_abstract', decision: 'maybe', reason: '中文，触觉通道融合，领域不同', by: '人工', at: ts('2026-10-08') },

    '10.1109/ICRA.2024.10611234': { stage: 'title_abstract', decision: 'exclude', reason: '领域不符（机器人操作），与空间转录组无关', by: '人工', at: ts('2026-10-08') },
    'arxiv:2506.02117': { stage: 'title_abstract', decision: 'exclude', reason: '领域不符，且为预印本', by: '人工', at: ts('2026-10-08') },
    '10.1016/j.cell.2024.06.021': { stage: 'title_abstract', decision: 'exclude', reason: '单细胞基础模型综述，不涉及空间配准误差', by: '人工', at: ts('2026-10-08') },
    '10.1038/s41592-022-01492-5': { stage: 'title_abstract', decision: 'exclude', reason: '批次整合基准，与空间配准不是同一问题；但作为方法学背景保留标记', by: '人工', at: ts('2026-10-08') },
    '10.1038/s41592-023-01902-3': { stage: 'title_abstract', decision: 'exclude', reason: '平台捕获效率比较，与注释可靠性无直接关系', by: '人工', at: ts('2026-10-08') },
    '10.1038/s41592-025-02204-9': { stage: 'title_abstract', decision: 'include', reason: '邻域分析避免分割误差，与误差传播问题相关', by: '人工', at: ts('2026-10-08') },
    '10.1038/s41467-024-48120-7': { stage: 'title_abstract', decision: 'include', reason: '配体受体推断的敏感性分析，邻域半径误差直接相关', by: '人工', at: ts('2026-10-08') },
    '10.1016/j.cels.2023.03.001': { stage: 'title_abstract', decision: 'exclude', reason: '无参解卷积，与参照图谱依赖性问题方向相反，暂排除', by: '人工', at: ts('2026-10-08') },
    '10.1016/j.ccell.2024.05.011': { stage: 'title_abstract', decision: 'exclude', reason: '肿瘤综述，领域具体但方法学贡献有限', by: '人工', at: ts('2026-10-08') },
    '10.1038/s41586-025-08841-2': { stage: 'title_abstract', decision: 'include', reason: '多模态联合嵌入，涉及域信号来源消融', by: '人工', at: ts('2026-10-08') },
    '10.1093/bioinformatics/btae102': { stage: 'title_abstract', decision: 'include', reason: '图拓扑对空间域识别的影响，可复现性相关', by: '人工', at: ts('2026-10-08') },
    '10.13345/j.cjb.230588': { stage: 'title_abstract', decision: 'exclude', reason: '质控指标清单，与配准误差无直接关系', by: '人工', at: ts('2026-10-08') },
    'arxiv:2503.04412': { stage: 'title_abstract', decision: 'maybe', reason: '预印本，不确定性量化思路可用但未经同行评审', by: '人工', at: ts('2026-10-08') },
  };

  /* 一条待复核的冲突：两位评审给出相反决定 */
  const conflicts = [
    {
      paperId: '10.1038/s41592-022-01492-5',
      field: 'title_abstract',
      decisions: [
        { by: '评审 A', decision: 'include', at: ts('2026-10-08'), reason: '批次整合的处理方式与空间配准同源，建议纳入方法学对照' },
        { by: '评审 B', decision: 'exclude', at: ts('2026-10-08'), reason: '批次效应与空间配准不是同一问题，纳入会稀释范围' },
      ],
      status: 'needs_review',
    },
  ];

  /* ---------------------------------------------------------------- PRISMA 账本
     计数单位是 record/report。研究层面的合并尚未实现，因此界面不能写「独立研究数」。 */
  const prisma = {
    identification: { records_from_databases: 347, records_from_other_sources: 0, duplicates_removed: 58, records_screened: 289 },
    // screening 各格在文件末尾由逐条决定派生 —— 手写数字必然与 decisions 漂移
    screening: { records_screened: 289, records_decided: 0, records_included: 0, records_excluded: 0, records_maybe: 0, records_pending: 0 },
    eligibility: { reports_assessed: 0, reports_excluded: 0, reports_included: 0 },
    included: { studies_included: 0, unit: 'record/report', study_level_merge_implemented: false },
    ledger: [
      { level: 'record', source: 'semantic_scholar', raw: 132, unique: 108, duplicates: 24 },
      { level: 'record', source: 'openalex', raw: 118, unique: 101, duplicates: 17 },
      { level: 'record', source: 'crossref', raw: 71, unique: 58, duplicates: 13 },
      { level: 'record', source: 'arxiv', raw: 26, unique: 22, duplicates: 4 },
    ],
    note: '账本从 provider 原始返回建账；研究层面（同一研究的多篇报告）合并尚未实现，因此 included 计数停留在 record/report 级别。全文阶段尚未开始，所以 eligibility 全为 0 而不是 0 被隐藏。',
  };

  /* ---------------------------------------------------------------- 证据版图 */
  const landscape = {
    sample_only: true,
    sample_size: 30,
    clusters: [
      { id: 0, name: '配准与注释迁移', terms: ['optimal transport', 'alignment', 'annotation transfer'], size: 7 },
      { id: 1, name: '解卷积与参考图谱', terms: ['deconvolution', 'reference atlas', 'composition'], size: 6 },
      { id: 2, name: '空间域与邻域结构', terms: ['spatial domain', 'neighbourhood', 'graph'], size: 6 },
      { id: 3, name: '基准与可复现性', terms: ['benchmark', 'reproducibility', 'reporting'], size: 6 },
      { id: 4, name: '相邻领域（引文扩展命中）', terms: ['VLA', 'manipulation', 'transfer'], size: 5 },
    ],
    years: null, // 运行时按语料派生
    // 形状对齐 landscape.novelty_scores()：{rows:[{paper_id,title,year,novelty,most_similar}], note}
    // 逐篇计算，且"没有更早文献可比较"时 novelty 必须是 null 而不是 0。运行时派生。
    novelty: { rows: [], note: '' },
    coverage: {
      note: '覆盖平衡只在当前样本内比较，样本外情况未知。',
      items: [
        { topic: '基准与可复现性', share: 0.20, target_share: 0.20, balance: 1.00 },
        { topic: '配准与注释迁移', share: 0.23, target_share: 0.20, balance: 0.87 },
        { topic: '解卷积与参考图谱', share: 0.20, target_share: 0.20, balance: 1.00 },
        { topic: '空间域与邻域结构', share: 0.20, target_share: 0.20, balance: 1.00 },
        { topic: '相邻领域（引文扩展命中）', share: 0.17, target_share: 0.20, balance: 0.83 },
      ],
    },
  };

  /* ---------------------------------------------------------------- 候选研究问题
     全部 status=hypothesis：样本空白不等于已证实的全球研究空白。 */
  const questions = [
    {
      kind: 'combination_gap', status: 'hypothesis', evidence_strength: 'moderate',
      title: '配准误差与解卷积误差是否存在耦合？',
      rationale: '样本内「最优传输配准」与「解卷积基准」两个主题簇几乎没有交叉引用：配准工作普遍报告注释迁移误差，但很少把它与解卷积的组成估计误差放在同一误差预算里讨论。这个组合缺口是样本内观察到的，不能用来说明整个领域没人做过。',
      coverage_gap: '配准 ∩ 解卷积 = 1 篇 / 7 × 6 篇',
      supporting_papers: ['10.1038/s41587-024-02251-x', '10.1038/s41592-024-02138-4', '10.13345/j.cjb.240312'],
      next_step: '建议用组合检索式（配准 AND 误差预算）在 Crossref 与 OpenAlex 复核',
    },
    {
      kind: 'unfollowed_result', status: 'hypothesis', evidence_strength: 'weak',
      title: '邻域半径敏感性是否被后续研究跟进？',
      rationale: '样本内唯一系统评估邻域半径敏感性的工作发表于 2024 年，其后两年没有被当前样本中的文献引用或扩展；但当前样本只有 30 篇，引文覆盖不完整，「无人跟进」很可能只是样本不足。',
      coverage_gap: '最近一次引用距今 0 个样本内引用',
      supporting_papers: ['10.1038/s41467-024-48120-7', '10.1038/s41592-025-02204-9'],
      next_step: '对该文献做一次正向引文扩展（建议 2 轮）再判断',
    },
    {
      kind: 'coverage_gap', status: 'hypothesis', evidence_strength: 'moderate',
      title: '中文文献在方法基准主题上占比偏低',
      rationale: '样本内「基准与可复现性」主题共 6 篇，其中中文文献 1 篇。这可能是检索式对中文库覆盖不足造成的样本偏差，而不是领域事实；Crossref 的中文检索式本轮还发生了降级（不支持相关度排序）。',
      coverage_gap: '基准与可复现性 share 0.20，其中中文 0.17',
      supporting_papers: ['10.13345/j.cjb.230588', '10.1186/s13059-025-03011-9', '10.1093/bioinformatics/btaf118'],
      next_step: '补充中文库检索式或提高中文来源上限后重跑',
    },
  ];

  /* ---------------------------------------------------------------- 会话列表 */
  const sessions = [
    { id: PROJECT_ID, topic: '单细胞与空间转录组的联合分析', direction: '用空间转录组验证单细胞注释的可靠性',
      papers: 30, phase: 'screening', updated_at: '2026-10-08T09:12:00+00:00', is_current: true,
      screening_status: 'preliminary_included', stop_reason: 'max_rounds', demo: true, schema_version: 1 },
    { id: 'prj-embodied-2026a', topic: '具身智能与机器人操作', direction: '跨本体迁移的评测协议',
      papers: 18, phase: 'search', updated_at: '2026-10-07T15:40:00+00:00', is_current: false,
      screening_status: 'not_started', stop_reason: 'budget_exhausted', demo: true, schema_version: 1 },
    { id: 'prj-wheat-2026b', topic: 'deep learning plant phenotyping', direction: 'image based wheat plant trait estimation',
      papers: 8, phase: 'expand', updated_at: '2026-10-06T11:05:00+00:00', is_current: false,
      screening_status: 'not_started', stop_reason: 'low_yield', demo: true, schema_version: 1 },
    { id: 'prj-corrupt-2026x', topic: '（会话文件损坏，已保留备份）', direction: '',
      papers: 0, phase: 'unknown', updated_at: '2026-10-05T08:20:00+00:00', is_current: false,
      screening_status: 'not_started', stop_reason: '', demo: false, schema_version: 1,
      corrupt: true, backup: 'prj-corrupt-2026x.corrupt-20261005T082000.bak',
      corrupt_reason: 'session_schema 校验失败：缺少 search_manifest 必需字段' },
  ];

  /* ---------------------------------------------------------------- 导出 */
  const exports_ = [
    { id: 'csv', name: 'CSV', scope: 'spreadsheet', fields: 'Paper 全部字段 + 来源轨迹摘要', note: '文本单元格做公式注入中和；原始文本保留在 JSON', accent: false },
    { id: 'json', name: 'JSON', scope: 'machine', fields: '规范化 ReviewState 全量', note: '保留原始文本，不做中和', accent: false },
    { id: 'ris', name: 'RIS', scope: 'reference_manager', fields: '标题/作者/年份/期刊/DOI/摘要', note: '可直接导入 Zotero、EndNote', accent: false },
    { id: 'bibtex', name: 'BibTeX', scope: 'reference_manager', fields: '同上，按文献类型生成条目', note: '预印本使用 @misc 并标注未同行评审', accent: false },
    { id: 'mermaid', name: 'Mermaid', scope: 'diagram', fields: '引文图结构（仅引文边）', note: '三类关系边不混入引文图', accent: false },
    { id: 'bundle', name: 'Evidence Pack (ZIP)', scope: 'evidence_bundle', fields: 'AGENT_HANDOFF.md + 结构化文件 + PRISMA 账本', note: '供后续 AI 或合作者接手使用', accent: true },
  ];

  const export_scopes = [
    { id: 'candidates', label: '候选集', count: 30, note: '当前项目语料的全部候选文献（未做人工决定）' },
    { id: 'preliminary', label: '初筛通过集', count: 0, note: '通过标题摘要筛选的记录；数量由逐条决定派生' },
    { id: 'final', label: '最终纳入集', count: 0, note: '全文阶段尚未完成，因此为空 —— 这是正确状态而非错误' },
    { id: 'selection', label: '当前选择', count: 3, note: '结果页上勾选的 3 篇' },
  ];

  /* ---------------------------------------------------------------- 任务 */
  const jobs = [
    { id: 'job-8f21a04c', kind: 'snowball_forward', status: 'partial', progress: { done: 2, total: 3, unit: '轮' },
      stop_reason: 'max_rounds', message: '达到轮数上限，已保留 2 轮成果与恢复位置', started: ts('2026-10-08'), cancellable: true },
    { id: 'job-3c9b17de', kind: 'search', status: 'completed', progress: { done: 4, total: 4, unit: '来源' },
      stop_reason: '', message: '四个来源正常返回，已合并去重', started: ts('2026-10-08'), cancellable: false },
    { id: 'job-71ea55b0', kind: 'download_fulltext', status: 'failed', progress: { done: 3, total: 8, unit: '篇' },
      stop_reason: 'api_failure', message: '3 篇已获取；其余 5 篇中 2 篇被付费墙拦截、3 篇来源超时。未获取不等于排除。', started: ts('2026-10-08'), cancellable: false },
    { id: 'job-2ad90f31', kind: 'landscape_build', status: 'running', progress: { done: 1, total: 3, unit: '视图' },
      stop_reason: '', message: '正在计算覆盖平衡视图', started: ts('2026-10-08'), cancellable: true },
  ];

  /* ---------------------------------------------------------------- 设置与诊断 */
  const settings = {
    providers: [
      { id: 'semantic_scholar', name: 'Semantic Scholar', configured: true, key_env: 'S2_API_KEY', rate_note: '未配置密钥时速率显著受限', docs: 'https://www.semanticscholar.org/product/api' },
      { id: 'openalex', name: 'OpenAlex', configured: false, key_env: 'OPENALEX_API_KEY', rate_note: '不配置密钥也可用，配置后每日额度更高', docs: 'https://openalex.org/settings/api' },
      { id: 'crossref', name: 'Crossref', configured: true, key_env: '', rate_note: '无需密钥；不支持相关度排序，按年份倒序返回', docs: 'https://api.crossref.org' },
      { id: 'arxiv', name: 'arXiv', configured: true, key_env: '', rate_note: '无需密钥；预印本，未同行评审', docs: 'https://info.arxiv.org/help/api' },
    ],
    preferences: {
      language: 'zh', auto_screen: false, review_mode: false,
      max_pdf_size_mib: 100, years_back: 5, max_papers: 200,
      scale_preset: 'standard',
    },
    diagnostics: [
      { level: 'info', at: ts('2026-10-08'), source: 'system', message: '离线演示模式；未发起任何真实网络请求。' },
      { level: 'warn', at: ts('2026-10-08'), source: 'crossref', message: '来源不支持相关度排序参数，已降级为按年份倒序。' },
      { level: 'warn', at: ts('2026-10-08'), source: 'semantic_scholar', message: '3 次限流（HTTP 429），已按退避重试 5 次。' },
      { level: 'error', at: ts('2026-10-08'), source: 'downloader', message: '2 篇全文获取失败：分别被付费墙拦截与超时。失败不构成排除理由。' },
      { level: 'info', at: ts('2026-10-08'), source: 'filters', message: '相关度在同一评分上下文内做了 min-max 归一化；跨上下文分数不可比较。' },
    ],
  };

  /* ---------------------------------------------------------------- 全文获取状态（按篇） */
  const retrieval = {
    '10.1038/s41592-024-02138-4': { status: 'retrieved', bytes: 2411724, pages: 14, provenance: 'publisher_pdf', at: ts('2026-10-08') },
    '10.13345/j.cjb.240312': { status: 'retrieved', bytes: 1188340, pages: 11, provenance: 'publisher_pdf', at: ts('2026-10-08') },
    '10.1038/s41587-024-02251-x': { status: 'retrieved', bytes: 3021884, pages: 18, provenance: 'publisher_pdf', at: ts('2026-10-08') },
    's2:20f5c1b6e8a9d3f4c7b2a1e5d8c3f0a9b6e2d4c1': { status: 'paywalled', bytes: 0, pages: 0, provenance: '', at: ts('2026-10-08'),
      note: '出版商未提供开放全文；元数据可用，全文不可得。' },
    '10.1038/s41576-023-00629-2': { status: 'paywalled', bytes: 0, pages: 0, provenance: '', at: ts('2026-10-08'), note: '订阅制期刊。' },
    'arxiv:2502.08871': { status: 'retrieved', bytes: 894220, pages: 22, provenance: 'arxiv_preprint', at: ts('2026-10-08'), note: '预印本，未经同行评审。' },
    'arxiv:2503.04412': { status: 'timeout', bytes: 0, pages: 0, provenance: '', at: ts('2026-10-08'), note: '来源超时，可重试。' },
    '10.11897/SP.J.1016.2025.01120': { status: 'too_large', bytes: 141557760, pages: 26, provenance: 'publisher_pdf', at: ts('2026-10-08'),
      note: '超过本机 100 MiB 上限，已拒绝下载并关闭连接。' },
    '10.1109/ICRA.2024.10611234': { status: 'blocked_redirect', bytes: 0, pages: 0, provenance: '', at: ts('2026-10-08'),
      note: '重定向跳转到非 HTTP(S) 协议，按地址校验规则中止。' },
  };

  /* =========================================================================
     派生计算 —— 分面、引文图、年份分布全部从 papers 推导，保证页面间一致
     ========================================================================= */

  const byId = new Map(papers.map((p) => [p.id, p]));

  function refsOf(p) { return (p.reference_ids || []).filter((r) => byId.has(r)); }

  /* 引文图：只用引文边（citation）。三类关系边单独一层，不混入。 */
  function buildCitationGraph(list) {
    const ids = new Set(list.map((p) => p.id));
    const links = [];
    list.forEach((p) => {
      (p.reference_ids || []).forEach((r) => { if (ids.has(r)) links.push({ source: p.id, target: r, type: 'citation' }); });
    });
    return { nodes: list.map((p) => p.id), links };
  }

  /* 三类关系边：bibliographic_coupling / co_citation / text_similarity
     —— 与引文边并列，不合并加权。 */
  function buildRelationEdges(list) {
    const out = { bibliographic_coupling: [], co_citation: [], text_similarity: [] };
    const refSets = new Map(list.map((p) => [p.id, new Set(refsOf(p))]));
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        const a = list[i].id, b = list[j].id;
        const shared = [...refSets.get(a)].filter((x) => refSets.get(b).has(x));
        if (shared.length) out.bibliographic_coupling.push({ a, b, weight: shared.length, evidence_ids: shared });
      }
    }
    // 共被引：语料内有第三方同时引用二者
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        const a = list[i].id, b = list[j].id;
        const citers = list.filter((p) => refsOf(p).includes(a) && refsOf(p).includes(b));
        if (citers.length) out.co_citation.push({ a, b, weight: citers.length, evidence_ids: citers.map((p) => p.id) });
      }
    }
    // 文本相似度：用主题标签重合度确定性推导，避免假随机
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        const a = list[i], b = list[j];
        const ta = new Set(a.topics), tb = new Set(b.topics);
        if (!ta.size || !tb.size) continue;
        const inter = [...ta].filter((x) => tb.has(x)).length;
        if (!inter) continue;
        const cosine = +(inter / Math.sqrt(ta.size * tb.size)).toFixed(4);
        if (cosine >= 0.5) out.text_similarity.push({ a: a.id, b: b.id, weight: cosine, evidence_ids: [], detail: { cosine } });
      }
    }
    return out;
  }

  function pagerank(list, links, iterations = 30, damping = 0.85) {
    const n = list.length;
    const rank = new Map(list.map((p) => [p.id, 1 / n]));
    const outDeg = new Map(list.map((p) => [p.id, 0]));
    links.forEach((l) => outDeg.set(l.source, (outDeg.get(l.source) || 0) + 1));
    for (let it = 0; it < iterations; it += 1) {
      const next = new Map(list.map((p) => [p.id, (1 - damping) / n]));
      links.forEach((l) => {
        const d = outDeg.get(l.source) || 1;
        next.set(l.target, next.get(l.target) + (damping * rank.get(l.source)) / d);
      });
      rank.clear();
      next.forEach((v, k) => rank.set(k, v));
    }
    return rank;
  }

  const citationGraph = buildCitationGraph(papers);
  const relationEdges = buildRelationEdges(papers);
  const rank = pagerank(papers, citationGraph.links);
  const centrality = papers
    .map((p) => ({ id: p.id, title: p.title, score: +rank.get(p.id).toFixed(5), citations: p.citation_count }))
    .sort((a, b) => b.score - a.score);

  // 连通分量近似社群（只跑引文边）
  function components(list, links) {
    const parent = new Map(list.map((p) => [p.id, p.id]));
    const find = (x) => { while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x))); x = parent.get(x); } return x; };
    links.forEach((l) => { const a = find(l.source), b = find(l.target); if (a !== b) parent.set(a, b); });
    const groups = new Map();
    list.forEach((p) => { const r = find(p.id); if (!groups.has(r)) groups.set(r, []); groups.get(r).push(p.id); });
    return [...groups.values()].sort((x, y) => y.length - x.length);
  }
  const communities = components(papers, citationGraph.links);

  /* 分面统计：全部基于当前项目**完整语料**，不是当前页 */
  function buildFacets(list) {
    const tally = (fn) => {
      const m = new Map();
      list.forEach((p) => {
        const keys = [].concat(fn(p));
        keys.forEach((k) => {
          const key = (k === null || k === undefined || k === '') ? '__unknown__' : k;
          m.set(key, (m.get(key) || 0) + 1);
        });
      });
      return [...m.entries()].map(([key, count]) => ({ key, count })).sort((a, b) => b.count - a.count);
    };

    const years = tally((p) => p.year);
    years.sort((a, b) => (a.key === '__unknown__' ? 1 : b.key === '__unknown__' ? -1 : a.key - b.key));

    const decision = (p) => (decisions[p.id] ? decisions[p.id].decision : 'undecided');

    return {
      scope_note: '以下统计基于当前项目的完整已获取语料（30 篇），不代表全球文献，也不是当前页统计。',
      years,
      sources: tally((p) => p.source),
      topics: tally((p) => p.topics),
      discovery: tally((p) => (p.discovery_traces || []).map((t) => t.method)),
      screening: [
        { key: 'include', count: list.filter((p) => decision(p) === 'include').length },
        { key: 'maybe', count: list.filter((p) => decision(p) === 'maybe').length },
        { key: 'exclude', count: list.filter((p) => decision(p) === 'exclude').length },
        { key: 'undecided', count: list.filter((p) => decision(p) === 'undecided').length },
      ],
      retrieval: [
        { key: 'retrieved', count: list.filter((p) => (retrieval[p.id] || {}).status === 'retrieved').length },
        { key: 'paywalled', count: list.filter((p) => (retrieval[p.id] || {}).status === 'paywalled').length },
        { key: 'timeout', count: list.filter((p) => (retrieval[p.id] || {}).status === 'timeout').length },
        { key: 'too_large', count: list.filter((p) => (retrieval[p.id] || {}).status === 'too_large').length },
        { key: 'blocked_redirect', count: list.filter((p) => (retrieval[p.id] || {}).status === 'blocked_redirect').length },
        { key: 'not_attempted', count: list.filter((p) => !retrieval[p.id]).length },
      ],
      score_contexts: [...new Set(list.map((p) => p.score_context_id))].map((ctx) => ({
        key: ctx, count: list.filter((p) => p.score_context_id === ctx).length,
      })),
    };
  }

  /* ---------------------------------------------------------------- 新颖度
     契约形状取自 landscape.novelty_scores()：**逐篇**计算，与更早文献的最大相似度
     之差；样本中没有更早文献可比较时 novelty 为 None（不是 0，也不是讨喜的 1.0），
     并且这类行排在最后。

     真实实现用 TF-IDF 余弦；原型用标题+摘要的词元 Jaccard 作代理，数值会不同，
     但字段名、可空性、排序规则与契约一致。 */
  function wordTokens(p) {
    const text = `${p.title} ${p.abstract || ''}`.toLowerCase();
    return new Set(text.replace(/[^a-z0-9\u4e00-\u9fff]+/g, ' ').split(' ')
      .filter((w) => w.length > 3));
  }
  function jaccard(a, b) {
    let inter = 0;
    a.forEach((t) => { if (b.has(t)) inter += 1; });
    const union = a.size + b.size - inter;
    return union ? inter / union : 0;
  }
  function buildNovelty(list) {
    const tokens = new Map(list.map((p) => [p.id, wordTokens(p)]));
    const rows = list.map((p) => {
      const earlier = list.filter((q) => q.id !== p.id && q.year != null && p.year != null && q.year < p.year);
      if (!earlier.length) {
        return {
          paper_id: p.id, title: p.title, year: p.year, novelty: null, most_similar: null,
          note: '样本中没有更早的文献可比较。No earlier paper in sample.',
        };
      }
      let best = null;
      earlier.forEach((q) => {
        const s = jaccard(tokens.get(p.id), tokens.get(q.id));
        if (!best || s > best.similarity) best = { paper_id: q.id, title: q.title, similarity: s };
      });
      return {
        paper_id: p.id, title: p.title, year: p.year,
        novelty: Number((1 - best.similarity).toFixed(4)),
        most_similar: { paper_id: best.paper_id, title: best.title, similarity: Number(best.similarity.toFixed(4)) },
      };
    });
    const scored = rows.filter((r) => r.novelty !== null).sort((a, b) => b.novelty - a.novelty);
    return {
      rows: scored.concat(rows.filter((r) => r.novelty === null)),
      note: '新颖度只表示在当前证据集内与更早文献的文本差异，不是同行评议的原创性评价。原型用词元 Jaccard 代理 TF-IDF 余弦，数值仅供界面演示。',
    };
  }

  const facets = buildFacets(papers);
  landscape.years = facets.years.filter((y) => y.key !== '__unknown__').map((y) => ({ year: Number(y.key), count: y.count }));
  landscape.novelty = buildNovelty(papers);

  /* ---------------------------------------------------------------- 计数派生
     筛选与 PRISMA 的每个数字都从 decisions 派生。手写计数必然会与逐条决定漂移
     （首版就出现了「声明纳入 11 篇、实际 15 篇」），所以一律不让它手写。 */
  const tally = { include: 0, exclude: 0, maybe: 0, undecided: 0 };
  papers.forEach((p) => { tally[decisions[p.id] ? decisions[p.id].decision : 'undecided'] += 1; });
  const decidedTotal = tally.include + tally.exclude + tally.maybe;

  screening.include = tally.include;
  screening.exclude = tally.exclude;
  screening.maybe = tally.maybe;
  screening.undecided = tally.undecided;
  screening.decided = decidedTotal;
  screening.total = papers.length;

  prisma.screening.records_decided = decidedTotal;
  prisma.screening.records_included = tally.include;
  prisma.screening.records_excluded = tally.exclude;
  prisma.screening.records_maybe = tally.maybe;
  prisma.screening.records_pending = prisma.identification.records_screened - decidedTotal;

  export_scopes.find((x) => x.id === 'preliminary').count = tally.include;

  /* 候选问题的规范排序（questions.py L409-413）：先按 kind 的固定次序，
     同一 kind 内 evidence_strength == 'moderate' 的排前面。 */
  const QUESTION_KIND_ORDER = { combination_gap: 0, coverage_gap: 1, unfollowed_result: 2 };
  questions.sort((a, b) => {
    const byKind = (QUESTION_KIND_ORDER[a.kind] ?? 9) - (QUESTION_KIND_ORDER[b.kind] ?? 9);
    if (byKind) return byKind;
    return (a.evidence_strength === 'moderate' ? 0 : 1) - (b.evidence_strength === 'moderate' ? 0 : 1);
  });

  /* 指向样本之外的引用是正常事实（论文会引用样本里没有的工作），不是数据错误。
     界面只展示样本内可追溯的引文，数量差异在这里如实记录。 */
  const externalRefs = [];
  papers.forEach((p) => (p.reference_ids || []).forEach((r) => { if (!byId.has(r)) externalRefs.push({ from: p.id, to: r }); }));

  /* 引文图里的边只允许指向语料内的节点 —— 这是不变量，不是约定。 */
  const badEdges = citationGraph.links.filter((l) => !byId.has(l.source) || !byId.has(l.target));

  /* ---------------------------------------------------------------- 导出给页面 */
  global.LED = {
    version: VERSION,
    demoTag: DEMO_TAG,
    projectId: PROJECT_ID,
    generatedAt: '2026-10-08',
    ctx: CTX,
    papers,
    duplicates,
    byId,
    manifest,
    snowball,
    http_budget,
    screening,
    decisions,
    conflicts,
    prisma,
    landscape,
    questions,
    sessions,
    exports: exports_,
    exportScopes: export_scopes,
    jobs,
    settings,
    retrieval,
    facets,
    graph: { citation: citationGraph, relations: relationEdges, communities, centrality },
    externalReferences: externalRefs,
    danglingGraphEdges: badEdges,
    tally,
    helpers: {
      refsOf,
      paperById: (id) => byId.get(id) || null,
      decisionOf: (p) => decisions[p.id] || null,
      retrievalOf: (p) => retrieval[p.id] || null,
      decisionTally: () => {
        const t = { include: 0, exclude: 0, maybe: 0, undecided: 0 };
        papers.forEach((p) => { t[decisions[p.id] ? decisions[p.id].decision : 'undecided'] += 1; });
        return t;
      },
      relationCounts: () => ({
        bibliographic_coupling: relationEdges.bibliographic_coupling.length,
        co_citation: relationEdges.co_citation.length,
        text_similarity: relationEdges.text_similarity.length,
      }),
      isCrossContext: (a, b) => a.score_context_id !== b.score_context_id,
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
