variable "project" {
  type        = string
  default     = "agentcore-bronto-demo"
  description = "Name prefix for driver resources."
}

variable "region" {
  type        = string
  default     = "eu-west-1"
  description = "AWS region (must match the AgentCore Runtime)."
}

variable "runtime_arn" {
  type        = string
  description = "ARN of the deployed AgentCore Runtime (from `agentcore status`). Set in terraform.tfvars."
}

# Account-specific values passed to the weekly patch CodeBuild as env vars so the
# repo stays free of account IDs / ARNs. Set real values in terraform.tfvars.
variable "collector_otlp_endpoint" {
  type        = string
  default     = ""
  description = "OTLP/HTTP endpoint of the collector ALB (terraform output collector_otlp_endpoint). The agent sends all telemetry here instead of directly to Bronto."
}

variable "agentcore_memory_id" {
  type        = string
  default     = ""
  description = "AgentCore Memory id (from provision_memory.py)."
}

variable "agentcore_semantic_strategy_id" {
  type        = string
  default     = ""
  description = "AgentCore Memory semantic strategy id (from provision_memory.py)."
}

variable "gateway_secret_arn" {
  type        = string
  default     = ""
  description = "Secrets Manager ARN holding the Gateway client_info (from provision_gateway.py)."
}

variable "schedule_expression" {
  type        = string
  default     = "rate(10 minutes)"
  description = "How often the periodic driver invokes the agent."
}

variable "bronto_dataset_url" {
  type        = string
  default     = ""
  description = "Optional deep link to the Bronto dataset, shown in the UI."
}

# --- Collector service (ECS Fargate + ALB) -----------------------------------
# The deployed AgentCore Runtime sends OTLP here instead of straight to
# Bronto; the collector holds the credentials and fans out to both accounts.

variable "bronto_otlp_base" {
  type        = string
  default     = "https://ingestion.eu.bronto.io"
  description = "First Bronto account's OTLP ingestion base URL (per-signal /v1/{logs,metrics,traces} appended by the collector)."
}

variable "bronto_api_key" {
  type        = string
  sensitive   = true
  description = "First Bronto account's ingestion API key. Pass via TF_VAR_bronto_api_key or -var; stored in Secrets Manager."
}

variable "bronto_otlp_base_2" {
  type        = string
  default     = ""
  description = "Second Bronto account's OTLP ingestion base URL. Leave blank until a second account is provisioned - the collector fans out to it in addition to (not instead of) the first account."
}

variable "bronto_api_key_2" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Second Bronto account's ingestion API key. Leave blank until a second account is provisioned."
}

variable "task_cpu" {
  type        = number
  default     = 512
}

variable "task_memory" {
  type        = number
  default     = 1024
}

variable "desired_count" {
  type        = number
  default     = 1
  description = "Number of collector ECS tasks to run."
}
