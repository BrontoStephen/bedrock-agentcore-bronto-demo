output "ui_url" {
  value       = aws_lambda_function_url.driver.function_url
  description = "Public URL of the on-demand demo UI."
}

output "driver_function" {
  value       = aws_lambda_function.driver.function_name
  description = "Driver Lambda name."
}
