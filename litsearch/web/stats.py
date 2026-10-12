"""运行概况聚合：首页访问量（PV）与检索入口点击次数。

需求约束（逐条对应实现）：

* **必须是后端持久化的全站聚合** —— 单个小 JSON 落在数据根目录，写盘用仓库既有的
  ``atomic_json``（同目录临时文件 + ``os.replace``）；不使用浏览器本地存储冒充全站累计值。
* **计数自正式启用时开始，并记录起始时间** —— 每个指标首次记数时写入 ``counting_since``；
  未开始记数的指标返回 ``None``，前端显示「—」，不编造历史数据。
* **上线时间来自持久化配置** —— 取值顺序：显式传入 → ``LEEXTRACTOR_LAUNCH_DATE`` →
  已持久化的值 → 默认值；首次写盘即固化，**重启不归零**，且**不使用进程/容器启动时间**。
  只提供日期时只按天数计算，不编造小时与分钟。
* **区分"上线至今"与"进程连续运行"** —— 前者按上线日期计算，后者是本次进程的运行时长，
  两者分开返回，避免混为一谈。
* **统计失败不阻塞首页与检索入口** —— 读失败退回内存视图，写失败仅标记 ``persisted=False``，
  绝不向调用方抛异常。
* **轻量** —— 一个 JSON 文件 + 进程内定长去重环，无后台线程、无定时器、无第三方依赖。

去重口径（避免组件重渲染、接口轮询造成重复累计）：客户端每次**页面进入**生成一个
``entry_id`` 并随请求上报；服务端在时间窗内忽略重复 ``entry_id``。未提供 ``entry_id`` 时按
"每次请求都是一次有效计数"处理（便于测试与命令行使用）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path

from litsearch.web.repository import atomic_json

logger = logging.getLogger(__name__)

#: 上线日期可通过环境变量配置（首次写盘后固化到 stats.json，后续以文件为准）。
LAUNCH_DATE_ENV = "LEEXTRACTOR_LAUNCH_DATE"
#: 已知的公网测试上线日期；没有更可靠的具体时刻时只按日期展示天数。
DEFAULT_LAUNCH_DATE = "2026-10-12"

METRIC_HOME_VIEWS = "home_views"
METRIC_SEARCH_CLICKS = "search_entry_clicks"
METRICS = (METRIC_HOME_VIEWS, METRIC_SEARCH_CLICKS)

#: 同一 entry_id 在该时间窗内只计一次。
DEDUPE_WINDOW_SECONDS = 6 * 60 * 60
#: 去重环上限（定长，超出即淘汰最旧项）。
DEDUPE_MAX_KEYS = 4096


def _parse_date(value: str | None) -> str | None:
    """Accept ``YYYY-MM-DD`` (or a full ISO timestamp) and return the date part."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        logger.warning("Ignoring invalid launch date %r", text)
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class StatsStore:
    """Persisted aggregate counters for the home page."""

    def __init__(self, root, launch_date: str | None = None) -> None:
        self.path = Path(root) / "stats.json"
        self._lock = threading.RLock()
        self._dedupe: OrderedDict[str, float] = OrderedDict()
        self._started_at = time.time()
        self._persisted = True
        self._data = self._load()
        configured = _parse_date(launch_date) or _parse_date(os.environ.get(LAUNCH_DATE_ENV)) or DEFAULT_LAUNCH_DATE
        # 已持久化的上线日期优先：重启、换配置都不会让"上线至今"归零或跳变。
        if not _parse_date(self._data.get("launch_date")):
            self._data["launch_date"] = configured
        for metric in METRICS:
            self._data[metric] = int(self._data.get(metric) or 0)
            since = self._data.get(f"{metric}_since")
            self._data[f"{metric}_since"] = str(since) if since else None
        self._data.setdefault("updated_at", None)

    # -- persistence -----------------------------------------------------

    def _load(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            logger.warning("Statistics file unreadable (%s); continuing without history", exc)
            return {}
        import json

        try:
            data = json.loads(raw)
        except ValueError:
            # 与项目既有约定一致：损坏文件先备份，再从头计数，绝不静默丢弃用户不知道的文件。
            try:
                backup = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}.bak")
                self.path.replace(backup)
                logger.warning("Statistics file was corrupt; backed up to %s", backup.name)
            except OSError:
                pass
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> bool:
        self._data["updated_at"] = _iso(_utc_now())
        try:
            atomic_json(self.path, self._data)
            self._persisted = True
            return True
        except Exception as exc:  # noqa: BLE001 - 统计失败绝不允许影响主页与检索
            if self._persisted:
                logger.warning("Statistics could not be persisted (%s); keeping in-memory counts", exc)
            self._persisted = False
            return False

    # -- counting --------------------------------------------------------

    def _seen_recently(self, entry_id: str, now: float) -> bool:
        seen = self._dedupe.get(entry_id)
        if seen is not None and now - seen <= DEDUPE_WINDOW_SECONDS:
            self._dedupe.move_to_end(entry_id)
            return True
        self._dedupe[entry_id] = now
        self._dedupe.move_to_end(entry_id)
        while len(self._dedupe) > DEDUPE_MAX_KEYS:
            self._dedupe.popitem(last=False)
        return False

    def record(self, metric: str, entry_id: str | None = None, *, now: float | None = None) -> bool:
        """Count one event. Returns True when the count actually increased."""
        if metric not in METRICS:
            raise ValueError(f"unknown metric: {metric}")
        moment = time.time() if now is None else now
        token = (entry_id or "").strip()
        with self._lock:
            if token and self._seen_recently(f"{metric}:{token}", moment):
                return False
            self._data[metric] = int(self._data.get(metric) or 0) + 1
            if not self._data.get(f"{metric}_since"):
                self._data[f"{metric}_since"] = _iso(_utc_now())
            self._save()
            return True

    # -- read model ------------------------------------------------------

    def snapshot(self, *, now: datetime | None = None) -> dict:
        moment = now or _utc_now()
        with self._lock:
            launch = _parse_date(self._data.get("launch_date")) or DEFAULT_LAUNCH_DATE
            try:
                days = max(0, (moment.date() - date.fromisoformat(launch)).days)
            except ValueError:  # pragma: no cover - guarded by _parse_date
                days = 0
            payload = {
                "launch": {"date": launch, "days": days, "precise_time": False},
                "runtime": {"process_uptime_seconds": round(max(0.0, time.time() - self._started_at), 1)},
                "persisted": self._persisted,
            }
            since_values = []
            for metric in METRICS:
                since = self._data.get(f"{metric}_since") or None
                if since:
                    since_values.append(since)
                payload[metric] = {
                    "count": int(self._data.get(metric) or 0) if since else None,
                    "counting_since": since,
                }
            payload["counting_since"] = min(since_values) if since_values else None
            return payload
