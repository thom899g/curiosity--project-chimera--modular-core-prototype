"""
Unified telemetry system with percentile tracking and real-time aggregation.
Every module MUST emit telemetry for observability.
"""
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Dict, Any, Optional, List
import time
import statistics

from firebase_admin import firestore
import firebase_admin

from .canonical_event import CanonicalEvent, EventType
from .system_clock import SystemClock


class TimerContext:
    """Context manager for timing operations with automatic telemetry emission."""
    
    def __init__(self, emitter: 'TelemetryEmitter', operation_name: str):
        self.emitter = emitter
        self.operation_name = operation_name
        self.start_time: Optional[float] = None
        
    def __enter__(self):
        self.start_time = time.perf_counter()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is not None:
            elapsed_ms = (time.perf_counter() - self.start_time) * 1000
            self.emitter.emit_latency(self.operation_name, elapsed_ms)


class TelemetryEmitter:
    """
    Unified telemetry collection with Firestore aggregation.
    Emits structured telemetry for real-time monitoring and alerting.
    
    Design Features:
    - Automatic percentile calculation (p50, p90, p99)
    - Error rate tracking
    - Resource usage monitoring
    - Firestore TTL for automatic cleanup
    """
    
    def __init__(self, module_name: str, firebase_credential_path: str = "serviceAccountKey.json"):
        """
        Initialize telemetry emitter for a specific module.
        
        Args:
            module_name: Name of the emitting module
            firebase_credential_path: Path to Firebase credentials
        """
        self.module = module_name
        
        if not firebase_admin._apps:
            from firebase_admin import credentials
            cred = credentials.Certificate(firebase_credential_path)
            firebase_admin.initialize_app(cred)
        
        self.db = firestore.client()
        self.clock = SystemClock()
        self._trace_stack: List[str] = []
        
        # In-memory aggregation for percentiles (bounded size)
        self._latency_samples: Dict[str, List[float]] = {}
        self._max_samples = 1000
        
    def emit_metric(self, 
                   name: str, 
                   value: float, 
                   tags: Optional[Dict[str, Any]] = None,
                   timestamp: Optional[datetime] = None) -> None:
        """
        Emit a metric with tags to Firestore telemetry collection.
        
        Args:
            name: Metric name (e.g., "memory_usage", "queue_size")
            value: Numeric value of the metric
            tags: Key-value pairs for filtering and aggregation
            timestamp: Optional custom timestamp (defaults to now)
        """
        try:
            event = CanonicalEvent(
                trace_id=self._current_trace(),
                sequence_key=self.clock.get_sequence_key(),
                event_type=EventType.TELEMETRY,
                created_at=timestamp or self.clock.now(),
                payload={
                    "module": self.module,
                    "metric": name,
                    "value": float(value),
                    "tags": tags or {},
                    "aggregated": False  # Will be aggregated by background job
                },
                metadata={
                    "source": self.module,
                    "telemetry_type": "metric"
                }
            )
            
            # Write to Firestore with document TTL
            doc_ref = self.db.collection("telemetry").document()
            doc_ref.set({
                **event.to_firestore_dict(),
                "_ttl": firestore.SERVER_TIMESTAMP  # Will be cleaned up by TTL policy
            })
            
            print(f"TELEMETRY [{self.module}] {name}={value} {tags or {}}")
            
        except Exception as e:
            print(f"ERROR: Failed to emit telemetry: {e}")
    
    def emit_latency(self, operation_name: str, latency_ms: float) -> None:
        """
        Emit latency measurement with automatic percentile tracking.
        
        Args:
            operation_name: Name of the timed operation
            latency_ms: Latency in milliseconds
        """
        # Store sample for percentile calculation
        if operation_name not in self._latency_samples:
            self._latency_samples[operation_name] = []
        
        samples = self._latency_samples[operation_name]
        samples.append(latency_ms)
        
        # Keep bounded history
        if len(samples) > self._max_samples:
            samples.pop(0)
        
        # Calculate percentiles if we have enough samples
        percentiles = {}
        if len(samples) >= 10:
            percentiles = {
                "p50": statistics.quantiles(samples, n=100)[49],
                "p90": statistics.quantiles(samples, n=100)[89],
                "p99": statistics.quantiles(samples, n=100)[98],
            }
        
        # Emit telemetry with percentiles
        self.emit_metric(
            name="operation_latency",
            value=latency_ms,
            tags={
                "operation": operation_name,
                "percentiles": percentiles,
                "sample_count": len(samples)
            }
        )
    
    def emit_error(self, 
                   error_type: