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

    async def get_metrics_summary(
        self,
        hours: int = 24,
        key_name: Optional[str] = None,
        range_type: Optional[str] = None,
        tz_offset_minutes: int = 0,
    ) -> Dict[str, Any]:
        """Aggregate metrics for dashboard telemetry, charts, and analysis tab."""
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        client_now = now_utc - timedelta(minutes=tz_offset_minutes)

        # Determine range and calendar boundaries
        if range_type == "today" or (range_type is None and hours == 24):
            client_start = client_now.replace(hour=0, minute=0, second=0, microsecond=0)
            client_end = client_start + timedelta(days=1)
            since = client_start + timedelta(minutes=tz_offset_minutes)
            until = client_end + timedelta(minutes=tz_offset_minutes)
            granularity = "hourly"
            num_buckets = 24
            bucket_labels = [f"{i:02d}:00" for i in range(24)]
            bucket_duration_sec = 3600.0
        elif range_type == "yesterday" or (range_type is None and hours == 48):
            client_yesterday_start = (client_now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            client_yesterday_end = client_yesterday_start + timedelta(days=1)
            since = client_yesterday_start + timedelta(minutes=tz_offset_minutes)
            until = client_yesterday_end + timedelta(minutes=tz_offset_minutes)
            granularity = "hourly"
            num_buckets = 24
            bucket_labels = [f"{i:02d}:00" for i in range(24)]
            bucket_duration_sec = 3600.0
            client_start = client_yesterday_start
            client_end = client_yesterday_end
        elif range_type == "7d" or (range_type is None and hours == 168):
            client_start = (client_now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
            client_end = client_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            since = client_start + timedelta(minutes=tz_offset_minutes)
            until = client_end + timedelta(minutes=tz_offset_minutes)
            granularity = "daily"
            num_buckets = 7
            bucket_labels = [(client_start + timedelta(days=i)).strftime("%m/%d") for i in range(7)]
            bucket_duration_sec = 86400.0
        elif range_type == "30d" or (range_type is None and hours == 720):
            client_start = (client_now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
            client_end = client_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            since = client_start + timedelta(minutes=tz_offset_minutes)
            until = client_end + timedelta(minutes=tz_offset_minutes)
            granularity = "daily"
            num_buckets = 30
            bucket_labels = [(client_start + timedelta(days=i)).strftime("%m/%d") for i in range(30)]
            bucket_duration_sec = 86400.0
        else:
            since = now_utc - timedelta(hours=hours)
            until = now_utc
            granularity = "hourly"
            num_buckets = 24
            bucket_labels = [f"{i:02d}:00" for i in range(24)]
            bucket_duration_sec = max(1.0, (until - since).total_seconds() / 24.0)
            client_start = since - timedelta(minutes=tz_offset_minutes)
            client_end = until - timedelta(minutes=tz_offset_minutes)
        filter_key = (key_name is not None and key_name != "all" and key_name.strip() != "")

        try:
            async with AsyncSessionLocal() as session:
                # 1. Global totals
                totals_query = select(
                    func.count(ApiUsageLog.id),
                    func.coalesce(func.sum(ApiUsageLog.char_count), 0),
                    func.coalesce(func.avg(ApiUsageLog.latency_ms), 0.0),
                ).where(ApiUsageLog.timestamp >= since, ApiUsageLog.timestamp < until)
                if filter_key:
                    totals_query = totals_query.where(ApiUsageLog.client_key_name == key_name)
                totals_res = await session.execute(totals_query)
                total_reqs, total_chars, avg_latency = totals_res.one()

                # Failed requests count in the window
                failed_query = select(func.count(ApiUsageLog.id)).where(
                    ApiUsageLog.timestamp >= since,
                    ApiUsageLog.timestamp < until,
                    ApiUsageLog.status_code >= 400,
                )
                if filter_key:
                    failed_query = failed_query.where(ApiUsageLog.client_key_name == key_name)
                failed_res = await session.execute(failed_query)
                failed_requests = int(failed_res.scalar() or 0)
                success_requests = max(0, int(total_reqs) - failed_requests)

                # Cache metrics (requests with latency < 15ms and status == 200 are SHA-256 audio cache hits)
                cache_hit_query = select(
                    func.count(ApiUsageLog.id),
                    func.coalesce(func.sum(ApiUsageLog.char_count), 0),
                ).where(
                    ApiUsageLog.timestamp >= since,
                    ApiUsageLog.timestamp < until,
                    ApiUsageLog.latency_ms < 15.0,
                    ApiUsageLog.status_code == 200,
                )
                if filter_key:
                    cache_hit_query = cache_hit_query.where(ApiUsageLog.client_key_name == key_name)
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
                    .where(ApiUsageLog.timestamp >= since, ApiUsageLog.timestamp < until)
                )
                if filter_key:
                    by_key_query = by_key_query.where(ApiUsageLog.client_key_name == key_name)
                by_key_query = by_key_query.group_by(ApiUsageLog.client_key_name).order_by(desc("chars"))
                by_key_res = await session.execute(by_key_query)
                usage_by_key = [
                    {"name": row.client_key_name, "requests": row.requests, "chars": row.chars}
                    for row in by_key_res.all()
                ]

                # 3. Usage by model/worker (excluding cache hits)
                by_model_query = (
                    select(
                        func.coalesce(ApiUsageLog.model_worker_id, "VieNeu-TTS v3").label("model"),
                        func.count(ApiUsageLog.id).label("requests"),
                        func.coalesce(func.sum(ApiUsageLog.char_count), 0).label("chars"),
                    )
                    .where(
                        ApiUsageLog.timestamp >= since,
                        ApiUsageLog.timestamp < until,
                        ApiUsageLog.model_worker_id != "cache-hit",
                    )
                )
                if filter_key:
                    by_model_query = by_model_query.where(ApiUsageLog.client_key_name == key_name)
                by_model_query = by_model_query.group_by(ApiUsageLog.model_worker_id).order_by(desc("chars"))
                by_model_res = await session.execute(by_model_query)
                rows = by_model_res.all()
                if rows:
                    usage_by_model = [
                        {"model": row.model or "VieNeu-TTS v3", "requests": int(row.requests or 0), "chars": int(row.chars or 0)}
                        for row in rows
                    ]
                else:
                    usage_by_model = [
                        {"model": "VieNeu-TTS v3", "requests": int(total_reqs), "chars": int(total_chars)}
                    ]

                # 4. Synchronized time buckets across the window (aligned to calendar bounds)
                time_buckets = []
                for i in range(num_buckets):
                    time_buckets.append({
                        "index": i,
                        "label": bucket_labels[i],
                        "requests": 0,
                        "success_requests": 0,
                        "failed_requests": 0,
                        "chars": 0,
                        "input_chars": 0,
                        "cached_chars": 0,
                        "raw_chars": 0,
                        "cache_hits": 0,
                        "avg_latency_ms": 0.0,
                        "latency_sum": 0.0,
                    })

                # 364 Discrete Activity Heatmap Blocks (52 columns x 7 rows) exactly like cpa-usage-keeper
                total_heatmap_blocks = 364
                total_span_sec = max(1.0, (client_end - client_start).total_seconds())
                cell_duration_sec = total_span_sec / float(total_heatmap_blocks)

                activity_blocks = []
                for idx in range(total_heatmap_blocks):
                    c_start = client_start + timedelta(seconds=idx * cell_duration_sec)
                    c_end = client_start + timedelta(seconds=(idx + 1) * cell_duration_sec)
                    activity_blocks.append({
                        "index": idx,
                        "time_label": f"{c_start.strftime('%m/%d %H:%M')} – {c_end.strftime('%H:%M')}",
                        "requests": 0,
                        "success": 0,
                        "failed": 0,
                        "chars": 0,
                        "cached_chars": 0,
                        "latency": 0.0,
                        "_lat_sum": 0.0,
                    })

                all_logs_query = (
                    select(
                        ApiUsageLog.timestamp,
                        ApiUsageLog.char_count,
                        ApiUsageLog.latency_ms,
                        ApiUsageLog.status_code,
                        ApiUsageLog.model_worker_id,
                        ApiUsageLog.client_key_name,
                    )
                    .where(ApiUsageLog.timestamp >= since, ApiUsageLog.timestamp < until)
                )
                if filter_key:
                    all_logs_query = all_logs_query.where(ApiUsageLog.client_key_name == key_name)
                all_logs_query = all_logs_query.order_by(ApiUsageLog.timestamp.asc())
                all_logs_res = await session.execute(all_logs_query)
                all_logs_list = all_logs_res.all()

                model_bucket_tokens: Dict[str, List[int]] = {}
                model_bucket_reqs: Dict[str, List[int]] = {}
                model_speed_samples: Dict[str, List[float]] = {}
                heatmap_counts: Dict[str, int] = {}
                latency_points = []
                all_latencies = []

                for row_ts, row_chars, row_lat, row_status, row_model, row_key in all_logs_list:
                    # Client timestamp aligned by timezone offset
                    client_ts = row_ts - timedelta(minutes=tz_offset_minutes)
                    chars_val = int(row_chars or 0)
                    lat_val = float(row_lat or 0.0)
                    model_str = row_model or "VieNeu-TTS v3"
                    key_str = row_key or "default"

                    if model_str not in model_bucket_tokens:
                        model_bucket_tokens[model_str] = [0] * num_buckets
                        model_bucket_reqs[model_str] = [0] * num_buckets

                    # Determine bucket index
                    offset_sec = (client_ts - client_start).total_seconds()
                    b_idx = int(offset_sec // bucket_duration_sec)
                    if 0 <= b_idx < num_buckets:
                        b = time_buckets[b_idx]
                        b["requests"] += 1
                        b["chars"] += chars_val
                        b["input_chars"] += chars_val
                        b["latency_sum"] += lat_val
                        if (row_status or 200) >= 400:
                            b["failed_requests"] += 1
                        else:
                            b["success_requests"] += 1

                        is_cache = (lat_val < 15.0) and (row_status == 200)
                        if is_cache:
                            b["cache_hits"] += 1
                            b["cached_chars"] += chars_val
                        else:
                            b["raw_chars"] += chars_val

                        model_bucket_tokens[model_str][b_idx] += chars_val
                        model_bucket_reqs[model_str][b_idx] += 1

                    # Discrete Heatmap Cell Mapping: One request maps to EXACTLY ONE cell
                    if 0 <= offset_sec < total_span_sec:
                        cell_idx = int(offset_sec // cell_duration_sec)
                        if 0 <= cell_idx < total_heatmap_blocks:
                            ab = activity_blocks[cell_idx]
                            ab["requests"] += 1
                            ab["chars"] += chars_val
                            ab["_lat_sum"] += lat_val
                            if (row_status or 200) >= 400:
                                ab["failed"] += 1
                            else:
                                ab["success"] += 1
                            if lat_val < 15.0 and (row_status == 200):
                                ab["cached_chars"] += chars_val

                    # Speed & Latency tracking
                    if lat_val >= 15.0 and chars_val > 0 and (row_status == 200):
                        speed_tps = round(chars_val / (lat_val / 1000.0), 1)
                        if model_str not in model_speed_samples:
                            model_speed_samples[model_str] = []
                        model_speed_samples[model_str].append(speed_tps)

                    all_latencies.append(lat_val)
                    if len(latency_points) < 200:
                        latency_points.append({
                            "x": chars_val,
                            "y": round(lat_val, 1),
                            "status": row_status,
                            "model": model_str,
                        })

                    # Heatmap tracking
                    heatmap_key = f"{key_str}___{model_str}"
                    heatmap_counts[heatmap_key] = heatmap_counts.get(heatmap_key, 0) + chars_val

                for b in time_buckets:
                    if b["requests"] > 0:
                        b["avg_latency_ms"] = round(b["latency_sum"] / b["requests"], 2)
                    del b["latency_sum"]

                for ab in activity_blocks:
                    if ab["requests"] > 0:
                        ab["latency"] = round(ab["_lat_sum"] / ab["requests"], 1)
                    del ab["_lat_sum"]

                # Hourly trends for overview chart
                hourly_trends = [
                    {
                        "time": b["label"],
                        "total_chars": b["chars"],
                        "requests": b["requests"],
                        "workers": {"VieNeu-TTS v3": b["chars"]},
                    }
                    for b in time_buckets
                ]

                # Top Models Series for Analysis
                model_usage_series = []
                for m_name, m_tokens in model_bucket_tokens.items():
                    model_usage_series.append({
                        "model": m_name,
                        "total_tokens": m_tokens,
                        "requests": model_bucket_reqs.get(m_name, [0] * num_buckets),
                    })
                if not model_usage_series:
                    model_usage_series = [{
                        "model": "VieNeu-TTS v3",
                        "total_tokens": [b["chars"] for b in time_buckets],
                        "requests": [b["requests"] for b in time_buckets],
                    }]

                # Top Models & API Key Composition for Analysis
                api_key_composition = []
                for k in usage_by_key:
                    k_chars = k["chars"]
                    k_reqs = k["requests"]
                    share = round((k_chars / max(1, total_chars)) * 100, 2)
                    api_key_composition.append({
                        "label": k["name"],
                        "total": k_chars,
                        "share": share,
                        "requests": k_reqs,
                        "cache_rate": cache_hit_rate,
                        "cost_usd": round((k_chars / 1_000_000.0) * 0.15, 4),
                        "rpm": round(k_reqs / minutes_window, 2),
                        "tpm": int(k_chars / minutes_window),
                    })

                model_composition = []
                for m in usage_by_model:
                    m_chars = m["chars"]
                    m_reqs = m["requests"]
                    share = round((m_chars / max(1, total_chars)) * 100, 2)
                    model_composition.append({
                        "label": m["model"],
                        "total": m_chars,
                        "share": share,
                        "requests": m_reqs,
                        "cache_rate": cache_hit_rate,
                        "cost_usd": round((m_chars / 1_000_000.0) * 0.15, 4),
                        "rpm": round(m_reqs / minutes_window, 2),
                        "tpm": int(m_chars / minutes_window),
                    })

                # Latency Percentiles
                all_latencies.sort()
                p95_lat = 0.0
                if all_latencies:
                    p95_idx = int(len(all_latencies) * 0.95)
                    p95_lat = round(all_latencies[min(len(all_latencies) - 1, p95_idx)], 1)

                # Model Speed averages
                model_speed_list = []
                for m_name, s_list in model_speed_samples.items():
                    avg_speed = round(sum(s_list) / len(s_list), 1) if s_list else 45.0
                    model_speed_list.append({
                        "model": m_name,
                        "speed_tps": avg_speed,
                        "sample_count": len(s_list),
                    })
                if not model_speed_list:
                    model_speed_list = [{"model": "VieNeu-TTS v3", "speed_tps": 48.5, "sample_count": total_reqs}]

                # Heatmap 2D Matrix
                unique_keys = list({k["name"] for k in usage_by_key}) or ["default"]
                unique_models = list({m["model"] for m in usage_by_model}) or ["VieNeu-TTS v3"]
                heatmap_rows = []
                for u_key in unique_keys:
                    row_cells = []
                    key_total = 0
                    for u_model in unique_models:
                        cell_chars = heatmap_counts.get(f"{u_key}___{u_model}", 0)
                        key_total += cell_chars
                        row_cells.append({
                            "model": u_model,
                            "chars": cell_chars,
                            "cost_usd": round((cell_chars / 1_000_000.0) * 0.15, 4),
                        })
                    heatmap_rows.append({
                        "api_key": u_key,
                        "models": row_cells,
                        "total_chars": key_total,
                        "total_cost": round((key_total / 1_000_000.0) * 0.15, 4),
                    })

                # Full Analysis Payload matching cpa-usage-keeper
                analysis_payload = {
                    "granularity": granularity,
                    "token_usage": [
                        {
                            "bucket": b["label"],
                            "input_tokens": b["chars"],
                            "output_tokens": b["raw_chars"],
                            "cache_read_tokens": b["cached_chars"],
                            "cache_creation_tokens": 0,
                            "reasoning_tokens": 0,
                            "total_tokens": b["chars"],
                            "requests": b["requests"],
                            "cost_usd": round((b["chars"] / 1_000_000.0) * 0.15, 4),
                            "cost_available": True,
                        }
                        for b in time_buckets
                    ],
                    "model_usage": {
                        "buckets": bucket_labels,
                        "series": model_usage_series,
                    },
                    "api_key_composition": api_key_composition,
                    "model_composition": model_composition,
                    "latency_diagnostics": {
                        "p95_latency_ms": p95_lat,
                        "avg_latency_ms": round(float(avg_latency), 1),
                        "total_points": len(all_latencies),
                        "points": latency_points,
                    },
                    "model_speed": {
                        "series": model_speed_list,
                    },
                    "heatmap": {
                        "models": unique_models,
                        "rows": heatmap_rows,
                    },
                    "cost_summary": {
                        "total_tokens": int(total_chars),
                        "total_cost_usd": round((int(total_chars) / 1_000_000.0) * 0.15, 4),
                        "cost_per_million": 0.15,
                    },
                }

                # 5. Recent logs (expanded to 100 items)
                recent_query = select(ApiUsageLog)
                if filter_key:
                    recent_query = recent_query.where(ApiUsageLog.client_key_name == key_name)
                recent_query = recent_query.order_by(desc(ApiUsageLog.id)).limit(100)
                recent_res = await session.execute(recent_query)
                recent_logs = [
                    {
                        "id": row.id,
                        "time": row.timestamp.isoformat(),
                        "key_name": row.client_key_name,
                        "endpoint": row.endpoint,
                        "worker": "Cache Hit (SHA-256)" if row.model_worker_id == "cache-hit" else (row.model_worker_id or "VieNeu-TTS v3"),
                        "chars": row.char_count,
                        "latency_ms": round(row.latency_ms, 2),
                        "status": row.status_code,
                        "is_cache_hit": (row.latency_ms < 15.0 and row.status_code == 200),
                    }
                    for row in recent_res.scalars().all()
                ]

                return {
                    "total_requests": int(total_reqs),
                    "failed_requests": failed_requests,
                    "success_requests": success_requests,
                    "total_chars": int(total_chars),
                    "average_latency_ms": round(float(avg_latency), 2),
                    "rpm": rpm,
                    "cpm": cpm,
                    "model_name": "VieNeu-TTS v3",
                    "cache_metrics": {
                        "cache_hit_count": cache_hits,
                        "cache_miss_count": raw_reqs,
                        "cache_hit_rate": cache_hit_rate,
                        "cached_chars": cached_chars,
                        "raw_chars": raw_chars,
                    },
                    "time_buckets": time_buckets,
                    "hourly_trends": hourly_trends,
                    "activity_blocks": activity_blocks,
                    "window_start": client_start.strftime("%m/%d %H:%M"),
                    "window_end": client_end.strftime("%m/%d %H:%M"),
                    "usage_by_key": usage_by_key,
                    "usage_by_model": usage_by_model,
                    "recent_logs": recent_logs,
                    "analysis": analysis_payload,
                    "granularity": granularity,
                }
        except Exception as e:
            logger.warning(f"Database query failed in get_metrics_summary ({e}), calculating from memory.")
            async with self._lock:
                logs = [l for l in self._in_memory_logs if l["timestamp"].replace(tzinfo=None) >= since]
                if filter_key:
                    logs = [l for l in logs if l.get("client_key_name") == key_name]
                total_reqs = len(logs)
                total_chars = sum(l["char_count"] for l in logs)
                avg_latency = (sum(l["latency_ms"] for l in logs) / total_reqs) if total_reqs > 0 else 0.0

                failed_requests = len([l for l in logs if l.get("status_code", 200) >= 400])
                success_requests = max(0, total_reqs - failed_requests)

                cache_hits = len([l for l in logs if l.get("latency_ms", 0.0) < 15.0 and l.get("status_code", 200) == 200])
                cached_chars = sum(l["char_count"] for l in logs if l.get("latency_ms", 0.0) < 15.0 and l.get("status_code", 200) == 200)
                cache_hit_rate = round((cache_hits / total_reqs) * 100, 2) if total_reqs > 0 else 0.0
                minutes_window = max(1, hours * 60)

                # Fallback 32 buckets
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                window_seconds = max(1, (now - since).total_seconds())
                bucket_duration_sec = window_seconds / 32.0
                time_buckets = []
                for i in range(32):
                    b_start = since + timedelta(seconds=i * bucket_duration_sec)
                    time_buckets.append({
                        "index": i,
                        "label": b_start.strftime("%H:%M"),
                        "requests": 0,
                        "success_requests": 0,
                        "failed_requests": 0,
                        "chars": 0,
                        "cache_hits": 0,
                        "avg_latency_ms": 0.0,
                    })

                for l in logs:
                    offset_sec = (l["timestamp"].replace(tzinfo=None) - since).total_seconds()
                    b_idx = min(31, max(0, int(offset_sec / bucket_duration_sec)))
                    b = time_buckets[b_idx]
                    b["requests"] += 1
                    b["chars"] += l.get("char_count", 0)
                    if l.get("status_code", 200) >= 400:
                        b["failed_requests"] += 1
                    else:
                        b["success_requests"] += 1
                    if l.get("latency_ms", 0.0) < 15.0 and l.get("status_code", 200) == 200:
                        b["cache_hits"] += 1

                return {
                    "total_requests": total_reqs,
                    "failed_requests": failed_requests,
                    "success_requests": success_requests,
                    "total_chars": total_chars,
                    "average_latency_ms": round(avg_latency, 2),
                    "rpm": round(total_reqs / minutes_window, 2),
                    "cpm": int(total_chars / minutes_window),
                    "model_name": "VieNeu-TTS v3",
                    "cache_metrics": {
                        "cache_hit_count": cache_hits,
                        "cache_miss_count": total_reqs - cache_hits,
                        "cache_hit_rate": cache_hit_rate,
                        "cached_chars": cached_chars,
                        "raw_chars": total_chars - cached_chars,
                    },
                    "time_buckets": time_buckets,
                    "hourly_trends": [
                        {
                            "time": b["label"],
                            "total_chars": b["chars"],
                            "requests": b["requests"],
                            "workers": {"VieNeu-TTS v3": b["chars"]},
                        }
                        for b in time_buckets
                    ],
                    "usage_by_key": [],
                    "usage_by_model": [{"model": "VieNeu-TTS v3", "requests": total_reqs, "chars": total_chars}],
                    "recent_logs": [
                        {
                            "id": l.get("id", idx + 1),
                            "time": l["timestamp"].isoformat(),
                            "key_name": l.get("client_key_name", "Anonymous"),
                            "endpoint": l.get("endpoint", "/v1/speech/synthesize"),
                            "worker": "Cache Hit (SHA-256)" if l.get("model_worker_id") == "cache-hit" else (l.get("model_worker_id") or "VieNeu-TTS v3"),
                            "chars": l.get("char_count", 0),
                            "latency_ms": round(l.get("latency_ms", 0.0), 2),
                            "status": l.get("status_code", 200),
                            "is_cache_hit": l.get("latency_ms", 0.0) < 15.0 and l.get("status_code", 200) == 200,
                        }
                        for idx, l in enumerate(logs[-100:])
                    ],
                    "analysis": {
                        "granularity": granularity,
                        "token_usage": [
                            {
                                "bucket": b["label"],
                                "input_tokens": b["chars"],
                                "output_tokens": b["chars"],
                                "cache_read_tokens": b["cache_hits"] * 100,
                                "cache_creation_tokens": 0,
                                "reasoning_tokens": 0,
                                "total_tokens": b["chars"],
                                "requests": b["requests"],
                                "cost_usd": round((b["chars"] / 1_000_000.0) * 0.15, 4),
                                "cost_available": True,
                            }
                            for b in time_buckets
                        ],
                        "model_usage": {
                            "buckets": [b["label"] for b in time_buckets],
                            "series": [{
                                "model": "VieNeu-TTS v3",
                                "total_tokens": [b["chars"] for b in time_buckets],
                                "requests": [b["requests"] for b in time_buckets],
                            }],
                        },
                        "api_key_composition": [],
                        "model_composition": [{
                            "label": "VieNeu-TTS v3",
                            "total": total_chars,
                            "share": 100.0,
                            "requests": total_reqs,
                            "cache_rate": cache_hit_rate,
                            "cost_usd": round((total_chars / 1_000_000.0) * 0.15, 4),
                            "rpm": round(total_reqs / minutes_window, 2),
                            "tpm": int(total_chars / minutes_window),
                        }],
                        "latency_diagnostics": {
                            "p95_latency_ms": round(avg_latency * 1.5, 1),
                            "avg_latency_ms": round(avg_latency, 1),
                            "total_points": total_reqs,
                            "points": [],
                        },
                        "model_speed": {
                            "series": [{"model": "VieNeu-TTS v3", "speed_tps": 48.5, "sample_count": total_reqs}],
                        },
                        "heatmap": {
                            "models": ["VieNeu-TTS v3"],
                            "rows": [],
                        },
                        "cost_summary": {
                            "total_tokens": total_chars,
                            "total_cost_usd": round((total_chars / 1_000_000.0) * 0.15, 4),
                            "cost_per_million": 0.15,
                        },
                    },
                    "granularity": granularity,
                }


usage_tracker = UsageTracker()
