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
variable "bronto_api_key_secret_arn" {
  type        = string
  default     = ""
  description = "Secrets Manager ARN holding the Bronto ingestion API key."
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
