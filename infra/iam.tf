data "aws_caller_identity" "current" {}

# ---- Execution role: pull images, write logs, read secrets/SSM ----
resource "aws_iam_role" "execution" {
  name = "${var.project}-exec"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow the execution role to read the Bronto secret and the collector config
# parameter so they can be injected as container secrets at start time.
resource "aws_iam_role_policy" "execution_secrets" {
  name = "${var.project}-exec-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.bronto_api_key.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameters", "ssm:GetParameter"]
        Resource = [aws_ssm_parameter.collector_config.arn]
      }
    ]
  })
}

# ---- Task role: what the app itself may call (Bedrock) ----
resource "aws_iam_role" "task" {
  name = "${var.project}-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Bedrock InvokeModel (Converse uses this action). Scoped to "*" because a
# cross-region inference profile fans out to foundation models across regions;
# tighten to specific profile + model ARNs for production.
resource "aws_iam_role_policy" "task_bedrock" {
  name = "${var.project}-task-bedrock"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:Converse",
        "bedrock:ConverseStream"
      ]
      Resource = "*"
    }]
  })
}
