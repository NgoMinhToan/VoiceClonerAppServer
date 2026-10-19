import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import desc, func, select

from VoiceClonerAppServer.core.database import AsyncSessionLocal
from VoiceClonerAppServer.models.db_models import ApiUsageLog

logger = logging.getLogger("VoiceClonerAppServer.UsageTracker")


class UsageTracker:
    def __init__(self):
        self._in_memory_logs: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def log_usage(
        self,
        client_key_id: str,
        client_key_name: str,
        endpoint: str,
        model_worker_id: Optional[str] = None,
        char_count: int = 0,
        latency_ms: float = 0.0,
        status_code: int = 200,
    ):
        """Asynchronously insert usage record into database with memory buffer fallback."""
        entry = {
            "timestamp": datetime.now(timezone.utc),
            "client_key_id": client_key_id,
            "client_key_name": client_key_name,
            "endpoint": endpoint,
            "model_worker_id": model_worker_id or "local",
            "char_count": char_count,
            "latency_ms": latency_ms,
            "status_code": status_code,
        }

        async with self._lock:
            self._in_memory_logs.append(entry)
            if len(self._in_memory_logs) > 5000:
                self._in_memory_logs = self._in_memory_logs[-2000:]

        try:
            async with AsyncSessionLocal() as session:
                log_row = ApiUsageLog(
                    client_key_id=client_key_id,
                    client_key_name=client_key_name,
                    endpoint=endpoint,
                    model_worker_id=model_worker_id,
                    char_count=char_count,
                    latency_ms=latency_ms,
                    status_code=status_code,
                )
                session.add(log_row)
                await session.commit()
        except Exception as e:
            logger.warning(f"Could not persist usage log to database ({e}). In-memory log retained.")

    async def get_metrics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Aggregate metrics for dashboard telemetry and charts."""
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)

        try:
            async with AsyncSessionLocal() as session:
                # 1. Global totals
                totals_query = select(
                    func.count(ApiUsageLog.id),
                    func.coalesce(func.sum(ApiUsageLog.char_count), 0),
                    func.coalesce(func.avg(ApiUsageLog.latency_ms), 0.0),
                ).where(ApiUsageLog.timestamp >= since)
                totals_res = await session.execute(totals_query)
                total_reqs, total_chars, avg_latency = totals_res.one()

                # Cache metrics (requests with latency < 15ms are SHA-256 audio cache hits)
                cache_hit_query = select(
                    func.count(ApiUsageLog.id),
                    func.coalesce(func.sum(ApiUsageLog.char_count), 0),
                ).where(ApiUsageLog.timestamp >= since, ApiUsageLog.latency_ms < 15.0)
                cache_hit_res = await session.execute(cache_hit_query)
                cache_hits, cached_chars = cache_hit_res.one()
                cache_hits = int(cache_hits or 0)
                cached_chars = int(cached_chars or 0)
                raw_reqs = max(0, int(total_reqs) - cache_hits)
                raw_chars = max(0, int(total_chars) - cached_chars)
                cache_hit_rate = round((cache_hits / int(total_reqs)) * 100, 2) if int(total_reqs) > 0 else 0.0

                # Throughput RPM and CPM
                minutes_window = max(1, hours * 60)
                rpm = round(int(total_reqs) / minutes_window, 2)
                cpm = int(int(total_chars) / minutes_window)

                # 2. Usage by key
                by_key_query = (
                    select(
                        ApiUsageLog.client_key_name,
                        func.count(ApiUsageLog.id).label("requests"),
                        func.sum(ApiUsageLog.char_count).label("chars"),
                    )
                    .where(ApiUsageLog.timestamp >= since)
                    .group_by(ApiUsageLog.client_key_name)
                    .order_by(desc("chars"))
                )
                by_key_res = await session.execute(by_key_query)
                usage_by_key = [
                    {"name": row.client_key_name, "requests": row.requests, "chars": row.chars}
                    for row in by_key_res.all()
                ]

                # 3. Usage by model
                by_model_query = (
                    select(
                        func.coalesce(ApiUsageLog.model_worker_id, "unknown").label("worker"),
                        func.count(ApiUsageLog.id).label("requests"),
                    )
                    .where(ApiUsageLog.timestamp >= since)
                    .group_by("worker")
                    .order_by(desc("requests"))
                )
                by_model_res = await session.execute(by_model_query)
                usage_by_model = [
                    {"model": row.worker, "requests": row.requests}
                    for row in by_model_res.all()
                ]

                # 4. Hourly trends
                # Fetch recent logs across the window to assemble hourly buckets
                trends_query = (
                    select(
                        ApiUsageLog.timestamp,
                        func.coalesce(ApiUsageLog.model_worker_id, "unknown").label("worker"),
                        ApiUsageLog.char_count,
                    )
                    .where(ApiUsageLog.timestamp >= since)
                    .order_by(ApiUsageLog.timestamp.asc())
                )
                trends_res = await session.execute(trends_query)
                hourly_buckets: Dict[str, Dict[str, Any]] = {}
                for row in trends_res.all():
                    hour_key = row.timestamp.strftime("%H:00")
                    if hour_key not in hourly_buckets:
                        hourly_buckets[hour_key] = {"time": hour_key, "total_chars": 0, "workers": {}}
                    hourly_buckets[hour_key]["total_chars"] += row.char_count
                    w = row.worker
                    hourly_buckets[hour_key]["workers"][w] = hourly_buckets[hour_key]["workers"].get(w, 0) + row.char_count
                hourly_trends = list(hourly_buckets.values())

                # 5. Recent logs (expanded to 50 items)
                recent_query = (
                    select(ApiUsageLog)
                    .order_by(desc(ApiUsageLog.id))
                    .limit(50)
                )
                recent_res = await session.execute(recent_query)
                recent_logs = [
                    {
                        "id": row.id,
                        "time": row.timestamp.isoformat(),
                        "key_name": row.client_key_name,
                        "endpoint": row.endpoint,
                        "worker": row.model_worker_id,
                        "chars": row.char_count,
                        "latency_ms": round(row.latency_ms, 2),
                        "status": row.status_code,
                        "is_cache_hit": row.latency_ms < 15.0,
                    }
                    for row in recent_res.scalars().all()
                ]

                return {
                    "total_requests": int(total_reqs),
                    "total_chars": int(total_chars),
                    "average_latency_ms": round(float(avg_latency), 2),
                    "rpm": rpm,
                    "cpm": cpm,
                    "cache_metrics": {
                        "cache_hit_count": cache_hits,
                        "cache_miss_count": raw_reqs,
                        "cache_hit_rate": cache_hit_rate,
                        "cached_chars": cached_chars,
                        "raw_chars": raw_chars,
                    },
                    "hourly_trends": hourly_trends,
                    "usage_by_key": usage_by_key,
                    "usage_by_model": usage_by_model,
                    "recent_logs": recent_logs,
                }
        except Exception as e:
            logger.warning(f"Database query failed in get_metrics_summary ({e}), calculating from memory.")
            async with self._lock:
                logs = [l for l in self._in_memory_logs if l["timestamp"].replace(tzinfo=None) >= since]
                total_reqs = len(logs)
                total_chars = sum(l["char_count"] for l in logs)
                avg_latency = (sum(l["latency_ms"] for l in logs) / total_reqs) if total_reqs > 0 else 0.0

                cache_hits = len([l for l in logs if l.get("latency_ms", 0.0) < 15.0])
                cached_chars = sum(l["char_count"] for l in logs if l.get("latency_ms", 0.0) < 15.0)
                cache_hit_rate = round((cache_hits / total_reqs) * 100, 2) if total_reqs > 0 else 0.0
                minutes_window = max(1, hours * 60)

                return {
                    "total_requests": total_reqs,
                    "total_chars": total_chars,
                    "average_latency_ms": round(avg_latency, 2),
                    "rpm": round(total_reqs / minutes_window, 2),
                    "cpm": int(total_chars / minutes_window),
                    "cache_metrics": {
                        "cache_hit_count": cache_hits,
                        "cache_miss_count": total_reqs - cache_hits,
                        "cache_hit_rate": cache_hit_rate,
                        "cached_chars": cached_chars,
                        "raw_chars": total_chars - cached_chars,
                    },
                    "hourly_trends": [],
                    "usage_by_key": [],
                    "usage_by_model": [],
                    "recent_logs": [
                        {**l, "time": l["timestamp"].isoformat(), "is_cache_hit": l.get("latency_ms", 0.0) < 15.0}
                        for l in logs[-50:]
                    ],
                }


usage_tracker = UsageTracker()
