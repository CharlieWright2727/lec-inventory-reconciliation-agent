"""Per-request and run-level operational cost metrics."""

from datetime import datetime

from pydantic import BaseModel, Field, computed_field


class ApiCallMetric(BaseModel):
    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    warehouse_id: str = Field(min_length=1)
    method: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    started_at: datetime
    latency_ms: float = Field(ge=0)
    status_code: int | None
    success: bool
    request_bytes: int = Field(ge=0)
    response_bytes: int = Field(ge=0)
    error_type: str | None = None


class RunMetrics(BaseModel):
    """Metrics for one run, with totals derived from individual calls."""

    api_calls: list[ApiCallMetric] = Field(default_factory=list)
    wall_clock_time_ms: float = Field(default=0, ge=0)

    @computed_field
    @property
    def total_api_calls(self) -> int:
        return len(self.api_calls)

    @computed_field
    @property
    def successful_calls(self) -> int:
        return sum(metric.success for metric in self.api_calls)

    @computed_field
    @property
    def failed_calls(self) -> int:
        return self.total_api_calls - self.successful_calls

    @computed_field
    @property
    def get_calls(self) -> int:
        return sum(metric.method.upper() == "GET" for metric in self.api_calls)

    @computed_field
    @property
    def put_calls(self) -> int:
        return sum(metric.method.upper() == "PUT" for metric in self.api_calls)

    @computed_field
    @property
    def catalogue_queries(self) -> int:
        return sum(
            metric.purpose == "catalogue_observation" for metric in self.api_calls
        )

    @computed_field
    @property
    def event_investigation_queries(self) -> int:
        return sum(
            metric.purpose == "event_investigation" for metric in self.api_calls
        )

    @computed_field
    @property
    def total_request_bytes(self) -> int:
        return sum(metric.request_bytes for metric in self.api_calls)

    @computed_field
    @property
    def total_response_bytes(self) -> int:
        return sum(metric.response_bytes for metric in self.api_calls)

    @computed_field
    @property
    def total_bytes_transferred(self) -> int:
        return self.total_request_bytes + self.total_response_bytes

    @computed_field
    @property
    def total_api_latency_ms(self) -> float:
        return sum(metric.latency_ms for metric in self.api_calls)


class MetricRecorder:
    """Run-scoped collector safe to use across concurrent asyncio tasks."""

    def __init__(self, metrics: RunMetrics | None = None) -> None:
        self.metrics = metrics or RunMetrics()

    def record(self, metric: ApiCallMetric) -> None:
        self.metrics.api_calls.append(metric)
