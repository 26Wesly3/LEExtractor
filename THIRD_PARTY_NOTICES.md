# Third-party notices

The repository source code is released under the [MIT License](LICENSE).
The items below are **not** covered by that licence and remain subject to their
own terms.

## Publication metadata and content

Metadata and abstracts returned by Semantic Scholar, OpenAlex, arXiv, Crossref,
OpenReview and Google Scholar (via SerpApi) belong to those services and to the
respective rights holders. Redistribution and reuse are governed by each
provider's terms of use. LEExtractor displays and exports what those APIs
return; it does not grant any additional rights to that content.

Full texts are only fetched from open-access sources (arXiv, OpenAlex OA,
Unpaywall); the downloader never uses shadow libraries.

## Model weights

| Model | Purpose | Licence |
| --- | --- | --- |
| `Helsinki-NLP/opus-mt-zh-en` (quantised Marian, ≈116 MB) | Chinese → English direction translation | CC-BY-4.0 |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (ONNX, ≈267 MB) | Local semantic reranking | Apache-2.0 |

Weights are downloaded at first use and cached locally; the minimal portable
package does not bundle them. See `litsearch/translation_pins.json` and
`litsearch/embedding_pins.json` for pinned revisions and SHA-256 inventories, and
`docs/models/` for the model cards.

## Benchmark data

The BEIR SciFact test collection, its qrels and the derived ranking snapshots in
`benchmarks/results/` are third-party research data. Cite the original sources:

- BEIR — <https://github.com/beir-cellar/beir>
- SciFact — <https://github.com/allenai/scifact>

## Fonts and images

`Anton` (in `web/public/assets/`) is bundled under the SIL Open Font License —
see `Anton-OFL.txt`. The ZJU wordmark and the Qiushi eagle character grid used
by the interface are identifiers of Zhejiang University and are included only
for this competition submission; they are not part of the MIT-licensed code.
