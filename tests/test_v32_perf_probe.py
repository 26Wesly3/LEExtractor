"""AC-26 probe: the fast-ranking path on the fixed 300-paper fixture.

This is a probe, not an acceptance of the budget: the test host is not the
2 vCPU / 2 GiB reference host of doc 15, so the result must say so and must
never claim that the target is met.  No network.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "v32_fast_rank_probe.py"


def _probe():
    spec = importlib.util.spec_from_file_location("_v32_fast_rank_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_default_output_lives_under_benchmarks_v32():
    module = _probe()
    assert module.DEFAULT_OUT.parent == REPO_ROOT / "benchmarks" / "v32"


def test_fast_ranking_probe_writes_labelled_timings(tmp_path):
    module = _probe()
    out = tmp_path / "benchmarks" / "v32" / "fast_ranking_probe.json"
    result = module.run(repetitions=3, out_path=out)

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written == result
    assert result["corpus_papers"] == 300
    assert result["ranked_items"] == 300
    assert result["host"] == "local host, not the reference host"
    assert result["measured_on_reference_host"] is False
    assert result["target_status"].startswith("not_assessed")
    assert result["reference_host"] == {"vcpu": 2, "memory_mib": 2048}
    assert result["network_used"] is False and result["semantic_model_used"] is False
    timings = result["fast_ranking_seconds"]
    assert timings["repetitions"] == 3
    assert 0 < timings["min"] <= timings["p50"] <= timings["p95"] <= timings["max"]
    # A probe on a non-reference host never claims the budget.
    text = json.dumps(result).lower()
    assert '"met"' not in text and "target_met" not in text and "passed" not in text
