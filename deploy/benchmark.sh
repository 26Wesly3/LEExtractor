#!/usr/bin/env bash
# LEExtractor v0.9.13 load and resource benchmark on the reference host
# (Ubuntu 24.04, 2 vCPU, 2 GB RAM). Run inside tmux on the server:
#
#   sudo bash /opt/leextractor/repo/deploy/benchmark.sh
#
# It starts from the conservative setting (MAX_ACTIVE_JOBS=1) and expands in
# both directions: idle -> 1 lexical -> 1 semantic -> 2 semantic -> 3 semantic.
# Every stage records container peak memory/CPU, host memory and swap, the
# kernel OOM counter and the final status of each job, so the table can be read
# without trusting anyone's summary. Result: /opt/leextractor/benchmark-result.md

set -uo pipefail

ROOT=${LEEXTRACTOR_ROOT:-/opt/leextractor}
B=http://127.0.0.1:8012
OUT=$ROOT/benchmark-result.md
CONTAINER=leextractor-leextractor-1
SAMPLE_EVERY=2
STAGE_TIMEOUT=900

: > "$OUT"
say() { echo "$@" | tee -a "$OUT"; }
host_mem() { free -m | awk '/^Mem:/{printf "used=%sMB avail=%sMB", $3, $7} /^Swap:/{printf " swap_used=%sMB", $3}'; }
cont() { docker stats --no-stream --format '{{.MemUsage}}|{{.CPUPerc}}' "$CONTAINER" 2>/dev/null | head -1; }
oom_count() { dmesg 2>/dev/null | grep -ci 'killed process' || echo 0; }

set_jobs() {
    sed -i "s/^LEEXTRACTOR_MAX_ACTIVE_JOBS=.*/LEEXTRACTOR_MAX_ACTIVE_JOBS=$1/" "$ROOT/.env"
    (cd "$ROOT" && docker compose up -d leextractor >/dev/null 2>&1)
    for _ in $(seq 1 40); do
        curl -sf "$B/api/health/live" >/dev/null 2>&1 && return 0
        sleep 2
    done
    say "!! app did not come back after setting MAX_ACTIVE_JOBS=$1"
    return 1
}

new_demo_project() {
    curl -s -X POST "$B/api/demo/projects" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin)["project_id"])'
}

start_search() {
    curl -s -X POST "$B/api/projects/$1/search" -H 'Content-Type: application/json' \
        -d "{\"expected_revision\":1,\"query\":\"$3\",\"ranking_mode\":\"$2\",\"max_papers\":60,\"year_from\":2015,\"year_to\":2026}" \
        | python3 -c 'import sys,json;print(json.load(sys.stdin).get("job_id",""))'
}

job_status() {
    curl -s "$B/api/jobs/$1" \
        | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("status","?"), "reason=" + str(d.get("stop_reason","")))' 2>/dev/null
}

stage() {
    local label=$1 cap=$2 mode=$3 n=$4
    local timeout=${5:-$STAGE_TIMEOUT}
    say ""
    say "### $label  (MAX_ACTIVE_JOBS=$cap, ranking=$mode, concurrent=$n, timeout=${timeout}s)"
    set_jobs "$cap" || return
    local ids=() i p
    for i in $(seq 1 "$n"); do
        p=$(new_demo_project)
        ids+=("$(start_search "$p" "$mode" "plant phenotyping roots")")
    done

    local peak_mem=0 peak_cpu=0 elapsed=0 active=0 c m cpu mb s j
    while :; do
        c=$(cont); m=${c%%|*}; cpu=${c##*|}
        mb=$(echo "$m" | awk '{print $1}' | tr -d 'MiB')
        awk -v a="${mb:-0}" -v b="$peak_mem" 'BEGIN{exit !(a>b)}' && peak_mem=${mb:-0}
        peak_cpu=$(echo "${cpu:-0} $peak_cpu" | awk '{v=$1+0; print (v>=$2)?v:$2}')
        sleep "$SAMPLE_EVERY"; elapsed=$((elapsed + SAMPLE_EVERY))
        active=0
        for j in ${ids[@]+"${ids[@]}"}; do
            s=$(job_status "$j")
            case "$s" in running*|queued*) active=$((active + 1));; esac
        done
        [ "$active" -eq 0 ] && break
        [ "$elapsed" -ge "$timeout" ] && { say "- 超过 ${timeout}s 仍未收敛，采样中止"; break; }
    done

    say "- 观测用时: ${elapsed}s"
    say "- 容器峰值内存: **${peak_mem} MiB** ; 容器峰值 CPU: **${peak_cpu}%**"
    say "- 宿主: $(host_mem)"
    say "- 内核 OOM 计数: $(oom_count)"
    for j in ${ids[@]+"${ids[@]}"}; do say "  - job ${j:0:8} -> $(job_status "$j")"; done
}

#: P6 soak, reduced: 500 jobs would take hours on a 2 GB host, so this is a
#: trend check that the container's memory returns to its baseline and that no
#: job is left running. Numbers are reported with the reduced count stated.
soak() {
    local count=${1:-20} i p j status failed=0 completed=0 before after
    say ""
    say "### S6 soak（${count} 次连续 lexical 作业，P6 的缩减版）"
    set_jobs 1 || return
    before=$(cont)
    for i in $(seq 1 "$count"); do
        p=$(new_demo_project)
        j=$(start_search "$p" lexical "plant phenotyping soak $i")
        for _ in $(seq 1 60); do
            status=$(job_status "$j")
            case "$status" in running*|queued*|"") sleep 2;; *) break;; esac
        done
        case "$status" in
            completed*|partial*) completed=$((completed + 1));;
            *) failed=$((failed + 1));;
        esac
    done
    sleep 5
    after=$(cont)
    say "- 完成 ${completed} / 失败 ${failed} （共 ${count}）"
    say "- 容器内存 before: ${before} → after: ${after}"
    say "- 宿主: $(host_mem) ; OOM 计数: $(oom_count)"
    say "- 判定: after 相对 before 无明显增长且 OOM 为 0，则无内存泄漏迹象"
}

say "# LEExtractor v0.9.13 资源与并发压测"
say ""
say "- 时间: $(date -Is)"
say "- 机器: $(nproc) vCPU / $(free -m | awk '/^Mem:/{print $2}') MB RAM / $(df -h / | awk 'NR==2{print $2}') disk / $(free -m | awk '/^Swap:/{print $2}') MB swap"
say "- 方法: 保守起点 MAX_ACTIVE_JOBS=1，向两边扩展；每 ${SAMPLE_EVERY}s 采样容器与宿主状态"
say "- 口径: 按 v0.9.14 DevLog 的 P7（并发 1/5/10/20 + CPU/RAM 峰值）与 P6（soak，缩减为 20 次）"
say "- 说明: semantic 首次会下载嵌入模型，因此 S2 同时是一次冷启动下载测量"

stage "S0 idle 基线"            1 lexical  0
stage "S1 1 × lexical"          1 lexical  1
stage "S2 1 × semantic"         1 semantic 1
stage "S3 2 × semantic"         2 semantic 2
stage "S4 5 × semantic（P7）"    5 semantic 5  420
stage "S5 10 × semantic（P7）"  10 semantic 10 420
stage "S5b 20 × semantic（P7）" 20 semantic 20 420
soak 20

set_jobs 1
say ""
say "## 判定要点（对照上面的数字）"
say "- 只要某阶段出现 swap 大幅增长、容器被内核杀死（OOM 计数增加）或任务大面积 failed，该阶段即**极端不可常态**。"
say "- 峰内存远低于宿主可用内存、且 swap 基本不动、任务全部 completed/partial 的阶段，即**可常态化运行**。"
say "- 最终建议值以各阶段实际数字为准，并写回 docker-compose.yml 与 .env.example。"
