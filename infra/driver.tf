# Driver Lambda: on-demand UI (Function URL) + periodic invocations (EventBridge).

data "archive_file" "driver" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.module}/builds/driver.zip"
}

resource "aws_iam_role" "driver" {
  name = "${var.project}-driver"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "driver" {
  name = "${var.project}-driver"
  role = aws_iam_role.driver.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid      = "InvokeRuntime"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeAgentRuntime"]
        Resource = [var.runtime_arn, "${var.runtime_arn}/*"]
      },
    ]
  })
}

resource "aws_lambda_function" "driver" {
  function_name    = "${var.project}-driver"
  role             = aws_iam_role.driver.arn
  runtime          = "python3.12"
  handler          = "driver.handler"
  filename         = data.archive_file.driver.output_path
  source_code_hash = data.archive_file.driver.output_base64sha256
  timeout          = 120
  memory_size      = 256

  environment {
    variables = {
      RUNTIME_ARN        = var.runtime_arn
      BRONTO_DATASET_URL = var.bronto_dataset_url
    }
  }
}

# Scheduled invocations are async; Lambda retries a failed async call twice by
# default, and each retry is another full, billed agent run. A missed tick is
# fine (the next one is 10 minutes away), so don't retry, and drop stale events.
resource "aws_lambda_function_event_invoke_config" "driver" {
  function_name                = aws_lambda_function.driver.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 600
}

# --- On-demand UI: IAM-authenticated Function URL ----------------------------
# Requests must be SigV4-signed by an IAM principal holding lambda:InvokeFunctionUrl
# (e.g. `awscurl --service lambda ...` or `aws lambda invoke-... `). No public surface.
resource "aws_lambda_function_url" "driver" {
  function_name      = aws_lambda_function.driver.function_name
  authorization_type = "AWS_IAM"
}

# --- Periodic invocations: EventBridge schedule ------------------------------
resource "aws_cloudwatch_event_rule" "periodic" {
  name                = "${var.project}-periodic"
  schedule_expression = var.schedule_expression
  description         = "Periodically invoke the AgentCore agent to keep telemetry flowing to Bronto."
}

resource "aws_cloudwatch_event_target" "periodic" {
  rule = aws_cloudwatch_event_rule.periodic.name
  arn  = aws_lambda_function.driver.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.driver.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.periodic.arn
}
