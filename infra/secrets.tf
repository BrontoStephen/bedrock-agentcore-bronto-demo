# Bronto ingestion keys -> Secrets Manager (consumed by the collector container).
# The collector broadcasts to both accounts; leave the second key blank until
# a second account is provisioned (see var.bronto_api_key_2).

resource "aws_secretsmanager_secret" "bronto_api_key" {
  name                    = "${var.project}/bronto-api-key"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "bronto_api_key" {
  secret_id     = aws_secretsmanager_secret.bronto_api_key.id
  secret_string = var.bronto_api_key
}

resource "aws_secretsmanager_secret" "bronto_api_key_2" {
  name                    = "${var.project}/bronto-api-key-2"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "bronto_api_key_2" {
  secret_id     = aws_secretsmanager_secret.bronto_api_key_2.id
  secret_string = var.bronto_api_key_2
}

# Collector config -> SSM Parameter. The ADOT collector loads it from the
# AOT_CONFIG_CONTENT env var, which we map from this parameter.
resource "aws_ssm_parameter" "collector_config" {
  name  = "/${var.project}/otel-collector-config"
  type  = "String"
  value = file("${path.module}/../collector/otel-collector-config.yaml")
}
