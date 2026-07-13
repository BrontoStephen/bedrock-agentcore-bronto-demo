output "ui_url" {
  value       = aws_lambda_function_url.driver.function_url
  description = "Public URL of the on-demand demo UI."
}

output "driver_function" {
  value       = aws_lambda_function.driver.function_name
  description = "Driver Lambda name."
}

output "collector_otlp_endpoint" {
  value       = "http://${aws_lb.collector.dns_name}"
  description = "OTLP/HTTP endpoint of the collector ALB. Feed this into the agent's OTEL_EXPORTER_OTLP_ENDPOINT (see scripts/redeploy.sh / scripts/deploy.env)."
}
