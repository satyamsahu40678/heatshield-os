"""Local synthetic-planning API. Operational approval is intentionally unsupported."""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from . import __version__
from .fixtures import demo_scenario
from .models import OptimizationResult, Scenario, ValidationReport, ValidationRequest
from .optimization import optimize
from .validation import validate_schedule

app = FastAPI(
    title="HeatShield OS — synthetic planning prototype",
    version=__version__,
    description=(
        "Stateless demonstration of constrained scheduling and independent validation. "
        "All policy thresholds are illustrative. Results require human review and cannot "
        "approve real work. The solve budget is at most five seconds; request time also "
        "includes model construction and independent validation."
    ),
)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/healthz")
def health():
    return {"status": "ok", "version": __version__, "mode": "synthetic"}


@app.get("/api/v1/demo/scenario", response_model=Scenario)
def demo():
    return demo_scenario()


@app.post("/api/v1/scenarios/validate", response_model=ValidationReport)
def validate(request: ValidationRequest):
    return validate_schedule(request.scenario, request.schedule)


@app.post("/api/v1/scenarios/optimize", response_model=OptimizationResult)
def optimization(scenario: Scenario):
    return optimize(scenario)
