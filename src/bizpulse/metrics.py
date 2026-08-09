"""BizPulse Thread-Safe Metrics Tracker."""

import threading

_lock = threading.Lock()

_METRICS = {
    "messages_seen": 0,
    "messages_filtered": 0,
    "llm_calls": 0,
    "extraction_calls": 0,
    "resolution_calls": 0,
    "followup_generation_calls": 0,
    "api_attempts": 0,
    "api_errors": 0,
    "low_signal_messages": 0,
    "clarification_responses": 0,
    "recovery_llm_calls": 0,
    "field_values_resolved_deterministically": 0,
    "onboarding_messages": 0,
    "clarification_requests": 0,
    "validation_attempts": 0,
    "invalid_responses": 0,
    "incomplete_commitments": 0
}

def increment(metric_name: str) -> None:
    """Thread-safely increment a metric count."""
    with _lock:
        if metric_name in _METRICS:
            _METRICS[metric_name] += 1

def get_metrics() -> dict[str, int]:
    """Thread-safely retrieve a copy of all current metrics."""
    with _lock:
        return _METRICS.copy()

def reset_metrics() -> None:
    """Thread-safely reset all metrics to zero."""
    with _lock:
        for k in _METRICS:
            _METRICS[k] = 0
