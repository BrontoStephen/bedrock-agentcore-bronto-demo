data "aws_caller_identity" "current" {}

# ---- Execution role: pull the collector image, write logs, read secrets/SSM ----
# Collector-only task, so no separate task role is needed (nothing in this
# stack calls AWS APIs at runtime beyond what the execution role fetches at
# container start).
resource "aws_iam_role" "collector_execution" {
  name = "${var.project}-collector-exec"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "collector_execution_managed" {
  role       = aws_iam_role.collector_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "collector_execution_secrets" {
  name = "${var.project}-collector-exec-secrets"
  role = aws_iam_role.collector_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.bronto_api_key.arn, aws_secretsmanager_secret.bronto_api_key_2.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameters", "ssm:GetParameter"]
        Resource = [aws_ssm_parameter.collector_config.arn]
      }
    ]
  })
}
