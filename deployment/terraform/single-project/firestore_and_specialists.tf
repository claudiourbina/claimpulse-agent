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

locals {
  specialist_roles = {
    intake   = "ClaimPulse Policy & Identity Intake Specialist"
    triage   = "ClaimPulse 3-Tier Severity & Reserve Triage Specialist"
    dispatch = "ClaimPulse Emergency Roadside & Adjuster Dispatch Specialist"
  }
}

# Serverless remote database for distributed Agent Runtime reasoning engines
resource "google_firestore_database" "claimpulse_db" {
  project                     = var.project_id
  name                        = "claimpulse-db"
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"

  depends_on = [google_project_service.services]
}

# Independent Vertex AI Agent Runtime Reasoning Engines for each specialist agent
resource "google_vertex_ai_reasoning_engine" "specialists" {
  for_each = local.specialist_roles

  display_name = "claimpulse-${each.key}-agent"
  description  = each.value
  region       = var.region
  project      = var.project_id

  spec {
    agent_framework = var.agent_framework
    service_account = google_service_account.app_sa.email
    identity_type   = "SERVICE_ACCOUNT"

    deployment_spec {
      min_instances         = 0
      max_instances         = 5
      container_concurrency = 8

      resource_limits = {
        cpu    = "2"
        memory = "4Gi"
      }

      env {
        name  = "CLAIM_PULSE_AGENT_ROLE"
        value = each.key
      }

      env {
        name  = "CLAIM_PULSE_STORAGE_BACKEND"
        value = "firestore"
      }

      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = "global"
      }

      env {
        name  = "GOOGLE_GENAI_USE_ENTERPRISE"
        value = "True"
      }

      env {
        name  = "OTEL_SERVICE_NAME"
        value = "claimpulse-${each.key}-agent"
      }
    }

    source_code_spec {
      inline_source {
        source_archive = local.dummy_source_b64
      }
      image_spec {}
    }
  }

  lifecycle {
    ignore_changes = [
      spec[0].container_spec,
      spec[0].source_code_spec,
      spec[0].deployment_spec,
    ]
  }

  depends_on = [
    google_project_service.services,
    google_firestore_database.claimpulse_db,
  ]
}
