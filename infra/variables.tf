variable "project" {
  type        = string
  default     = "bedrock-bronto-demo"
  description = "Name prefix for all resources."
}

variable "region" {
  type        = string
  default     = "eu-west-1"
  description = "AWS region to deploy into."
}

variable "bedrock_model_id" {
  type        = string
  default     = "eu.amazon.nova-micro-v1:0"
  description = "Bedrock model / inference-profile id the app invokes via Converse."
}

variable "bronto_otlp_base" {
  type        = string
  default     = "https://ingestion.eu.bronto.io"
  description = "Bronto OTLP ingestion base URL (per-signal /v1/{logs,metrics,traces} appended by the collector)."
}

variable "bronto_api_key" {
  type        = string
  sensitive   = true
  description = "Bronto ingestion API key. Pass via TF_VAR_bronto_api_key or -var; stored in Secrets Manager."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Tag of the app image in ECR to deploy."
}

variable "desired_count" {
  type        = number
  default     = 1
  description = "Number of ECS tasks to run."
}

variable "task_cpu" {
  type        = number
  default     = 512
}

variable "task_memory" {
  type        = number
  default     = 1024
}
