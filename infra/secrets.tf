# Bronto ingestion key -> Secrets Manager (consumed by the collector container).
resource "aws_secretsmanager_secret" "bronto_api_key" {
  name                    = "${var.project}/bronto-api-key"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "bronto_api_key" {
  secret_id     = aws_secretsmanager_secret.bronto_api_key.id
  secret_string = var.bronto_api_key
}

# Collector config -> SSM Parameter. The ADOT collector loads it from the
# AOT_CONFIG_CONTENT env var, which we map from this parameter.
resource "aws_ssm_parameter" "collector_config" {
  name  = "/${var.project}/otel-collector-config"
  type  = "String"
  value = file("${path.module}/../collector/otel-collector-config.yaml")
}
