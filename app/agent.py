# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import logging
import os

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app.discovery.model_router import ClaimPulseSafetyGuardrailPlugin
from app.discovery.registry_discovery import get_discovery_service
from app.telemetry.tracer import configure_telemetry, record_metric_event
from app.tools import (
    assess_claim_severity,
    discover_available_specialists,
    dispatch_roadside_assistance,
    get_claim_status_and_history,
    schedule_adjuster_and_workshop,
    verify_policy_and_coverage,
)

configure_telemetry()

MODEL = "gemini-3.8-flash"


async def initialize_claimpulse_state(callback_context: CallbackContext) -> None:
    """Initializes session state keys and records agent invocation telemetry."""
    if "session_initialized" not in callback_context.state:
        callback_context.state["session_initialized"] = True
        callback_context.state["deployment_mode"] = os.environ.get(
            "CLAIM_PULSE_DEPLOYMENT_MODE", "local"
        )
    record_metric_event(
        "agent.turn.start",
        {
            "agent_name": callback_context.agent_name,
            "verified_policy_number": callback_context.state.get(
                "verified_policy_number", "none"
            ),
        },
    )
    return None


def create_intake_specialist() -> Agent:
    """Factory for the ClaimPulse Policy & Identity Verification Specialist Agent."""
    return Agent(
        name="claimpulse_intake_specialist",
        model=Gemini(
            model=MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        description=(
            "Specialist agent for verifying insurance policies, deductibles, "
            "coverage perils, and past claim history."
        ),
        instruction="""You are the ClaimPulse Policy & Identity Verification Specialist.
Current verified policy: {verified_policy_number?}
Current policyholder: {holder_name?}

Responsibilities:
1. Always call `verify_policy_and_coverage` when a policy number (e.g., POL-CL-1001) is provided.
2. Call `get_claim_status_and_history` when the user asks about existing claims or remembered history.
3. Never invent coverage terms or emit raw HTML/JavaScript; rely strictly on tool results.
""",
        tools=[verify_policy_and_coverage, get_claim_status_and_history],
        before_agent_callback=initialize_claimpulse_state,
    )


def create_triage_specialist() -> Agent:
    """Factory for the ClaimPulse 3-Tier Severity & Reserve Triage Specialist Agent."""
    return Agent(
        name="claimpulse_triage_specialist",
        model=Gemini(
            model=MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        description=(
            "Specialist agent that evaluates claim severity (1-10), drivable status, "
            "and injuries to classify claims into Tier 1, Tier 2, or Tier 3."
        ),
        instruction="""You are the ClaimPulse 3-Tier Clinical & Insurance Severity Triage Specialist.
Current verified policy: {verified_policy_number?}
Active claim ID: {active_claim_id?}

Responsibilities:
1. Use `verify_policy_and_coverage` if the policy has not been verified yet.
2. Call `assess_claim_severity` to classify the incident into:
   - Tier 1 (Fast-Track Self-Service, severity 1-3, minor cosmetic/glass, drivable, no injuries)
   - Tier 2 (Certified Adjuster & Workshop Inspection, severity 4-7, moderate collision/property damage)
   - Tier 3 (Critical Emergency & Roadside Dispatch, severity 8-10, non-drivable, smoke/fire, or injuries)
3. Clearly communicate the assigned Tier, deductible, estimated repair cost, net payout, and safety steps.
""",
        tools=[verify_policy_and_coverage, assess_claim_severity],
        before_agent_callback=initialize_claimpulse_state,
    )


def create_dispatch_specialist() -> Agent:
    """Factory for the ClaimPulse Roadside Emergency & Adjuster Dispatch Specialist Agent."""
    return Agent(
        name="claimpulse_dispatch_specialist",
        model=Gemini(
            model=MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        description=(
            "Specialist agent that dispatches emergency tow trucks for Tier 3 claims "
            "and schedules accredited adjusters and workshops with Google Calendar and Meet links."
        ),
        instruction="""You are the ClaimPulse Emergency Roadside & Adjuster Dispatch Specialist.
Active claim ID: {active_claim_id?}
Last triage tier: {last_triage_tier?}
Recommended specialty: {recommended_specialty?}

Responsibilities:
1. For Tier 3 critical emergencies or immobilized vehicles, immediately call `dispatch_roadside_assistance` and highlight life-safety instructions (911 / 131 / 133) plus the live Google Meet emergency adjuster bridge.
2. For Tier 2 inspections or workshop requests, call `schedule_adjuster_and_workshop` and share the confirmed adjuster name, workshop address, Google Calendar sync link, and Google Meet room URL.
""",
        tools=[schedule_adjuster_and_workshop, dispatch_roadside_assistance],
        before_agent_callback=initialize_claimpulse_state,
    )


def build_root_agent_for_role(role: str | None = None) -> Agent:
    """Builds the root ADK Agent based on `CLAIM_PULSE_AGENT_ROLE`.

    Allows the same container image to be deployed as 4 independent Reasoning Engines
    on Vertex AI Agent Runtime (`supervisor`, `intake`, `triage`, `dispatch`), while
    preserving `name="claimpulse_agent"` for the served root agent contract.
    """
    active_role = (
        role or os.environ.get("CLAIM_PULSE_AGENT_ROLE", "supervisor")
    ).strip().lower()

    if active_role == "intake":
        specialist = create_intake_specialist()
        specialist.name = "claimpulse_agent"
        return specialist
    if active_role == "triage":
        specialist = create_triage_specialist()
        specialist.name = "claimpulse_agent"
        return specialist
    if active_role == "dispatch":
        specialist = create_dispatch_specialist()
        specialist.name = "claimpulse_agent"
        return specialist

    # Default: Supervisor orchestrator with dynamic Agent Runtime discovery
    discovery_service = get_discovery_service()
    remote_sub_agents = discovery_service.build_remote_a2a_sub_agents()
    sub_agents = (
        remote_sub_agents
        if remote_sub_agents
        else [
            create_intake_specialist(),
            create_triage_specialist(),
            create_dispatch_specialist(),
        ]
    )

    return Agent(
        # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
        # from the project `name:` recorded there, and telemetry reports it as
        # gen_ai.agent.name.
        name="claimpulse_agent",
        model=Gemini(
            model=MODEL,
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
        description=(
            "ClaimPulse Supervisor Agent: orchestrates policy intake, 3-tier severity "
            "triage, adjuster scheduling, and emergency roadside dispatch across "
            "Vertex AI Agent Runtime specialists."
        ),
        instruction="""You are ClaimPulse, an Autonomous Insurance Claim Intake, 3-Tier Severity Triage & Emergency Roadside Dispatch Supervisor built on Gemini Enterprise Agent Platform.

Session Context:
- Verified Policy: {verified_policy_number?}
- Policyholder: {holder_name?}
- Active Claim ID: {active_claim_id?}
- Last Triage Tier: {last_triage_tier?}
- Compacted Session Summary: {compacted_history_summary?}

Core Operating Protocol:
1. **Dynamic Specialist Discovery**: If the user asks which specialist agents are available or active in Agent Runtime / Agent Registry, call `discover_available_specialists`.
2. **Policy Verification First**: When a user reports an incident with a policy number (e.g., POL-CL-1001, POL-CL-1002, POL-CL-1003), verify the policy using `verify_policy_and_coverage`.
3. **Deterministic 3-Tier Triage**: Evaluate any reported damage or incident by calling `assess_claim_severity`:
   - **Tier 1 (Severity 1-3, minor cosmetic/glass, drivable, no injuries)**: Present the Fast-Track digital payout breakdown (estimated repair, deductible, net payout) and express workshop options.
   - **Tier 2 (Severity 4-7, moderate collision or property damage)**: After `assess_claim_severity`, also call `schedule_adjuster_and_workshop` to assign an accredited adjuster, workshop, Google Calendar link, and Google Meet link.
   - **Tier 3 (Severity 8-10, immobilized vehicle, smoke/fire, highway hazard, or injuries)**: Immediately prioritize life safety (911 / 131 SAMU / 133), call `assess_claim_severity`, and immediately call `dispatch_roadside_assistance` to dispatch a tow truck with live ETA and an Emergency Adjuster Google Meet bridge.
4. **Core UI Invariant**: Never output raw HTML or `<script>` tags. Always summarize clearly in the user's language (Spanish or English) using the structured data returned by the tools.
""",
        tools=[
            discover_available_specialists,
            verify_policy_and_coverage,
            assess_claim_severity,
            schedule_adjuster_and_workshop,
            dispatch_roadside_assistance,
            get_claim_status_and_history,
        ],
        sub_agents=sub_agents,
        before_agent_callback=initialize_claimpulse_state,
    )


root_agent = build_root_agent_for_role()

# Initialize Runner-wide Plugins (Safety Guardrail + BigQuery Agent Analytics)
_plugins = [ClaimPulseSafetyGuardrailPlugin(model_name=MODEL)]
_project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
_dataset_id = os.environ.get("BQ_ANALYTICS_DATASET_ID", "adk_agent_analytics")
_location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

if _project_id:
    try:
        from google.adk.plugins.bigquery_agent_analytics_plugin import (
            BigQueryAgentAnalyticsPlugin,
            BigQueryLoggerConfig,
        )
        from google.cloud import bigquery

        bq = bigquery.Client(project=_project_id)
        bq.create_dataset(f"{_project_id}.{_dataset_id}", exists_ok=True)

        _plugins.append(
            BigQueryAgentAnalyticsPlugin(
                project_id=_project_id,
                dataset_id=_dataset_id,
                location=_location,
                config=BigQueryLoggerConfig(
                    gcs_bucket_name=os.environ.get("BQ_ANALYTICS_GCS_BUCKET"),
                    connection_id=os.environ.get("BQ_ANALYTICS_CONNECTION_ID"),
                ),
            )
        )
    except Exception as e:
        logging.warning(f"Failed to initialize BigQuery Analytics: {e}")

app = App(
    root_agent=root_agent,
    name="app",
    plugins=_plugins,
)
