# Weekly patch pipeline: CodeBuild re-runs the deploy (reinstalling latest deps)
# on a schedule, keeping the runtime's dependencies ahead of the vuln scanner.

# --- Source snapshot of the demo (agent code + buildspec + redeploy script) ---
resource "aws_s3_bucket" "source" {
  bucket        = "${var.project}-cicd-source-${data.aws_caller_identity.me.account_id}"
  force_destroy = true
}

data "aws_caller_identity" "me" {}

data "archive_file" "source" {
  type        = "zip"
  source_dir  = "${path.module}/.."
  output_path = "${path.module}/builds/source.zip"
  excludes = [
    "infra", "infra/**", "**/.terraform/**", "**/__pycache__/**",
    "**/.venv/**", "**/acvenv/**", ".env", "**/*.pyc",
    "**/deploy.env", "**/*.tfvars",
  ]
}

resource "aws_s3_object" "source" {
  bucket = aws_s3_bucket.source.id
  key    = "source.zip"
  source = data.archive_file.source.output_path
  etag   = data.archive_file.source.output_md5
}

# --- CodeBuild role ----------------------------------------------------------
resource "aws_iam_role" "codebuild" {
  name = "${var.project}-codebuild"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "codebuild" {
  name = "${var.project}-codebuild"
  role = aws_iam_role.codebuild.id
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
        Sid      = "SourceAndDeployBuckets"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation", "s3:CreateBucket"]
        Resource = ["${aws_s3_bucket.source.arn}", "${aws_s3_bucket.source.arn}/*", "arn:aws:s3:::bedrock-agentcore-codebuild-sources-*", "arn:aws:s3:::bedrock-agentcore-codebuild-sources-*/*"]
      },
      {
        Sid      = "DeployAgentCore"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:*", "bedrock-agentcore-control:*"]
        Resource = "*"
      },
      {
        Sid      = "PassExecRole"
        Effect   = "Allow"
        Action   = ["iam:PassRole", "iam:GetRole"]
        Resource = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:role/AmazonBedrockAgentCoreSDKRuntime-*"
      },
    ]
  })
}

resource "aws_codebuild_project" "patch" {
  name         = "${var.project}-weekly-patch"
  service_role = aws_iam_role.codebuild.arn

  artifacts { type = "NO_ARTIFACTS" }

  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/standard:7.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = false
    environment_variable {
      name  = "AWS_REGION"
      value = var.region
    }
    # Account-specific deploy config supplied to redeploy.sh at build time, so the
    # repo itself carries no account IDs / ARNs.
    environment_variable {
      name  = "BRONTO_API_KEY_SECRET_ARN"
      value = var.bronto_api_key_secret_arn
    }
    environment_variable {
      name  = "AGENTCORE_MEMORY_ID"
      value = var.agentcore_memory_id
    }
    environment_variable {
      name  = "AGENTCORE_SEMANTIC_STRATEGY_ID"
      value = var.agentcore_semantic_strategy_id
    }
    environment_variable {
      name  = "GATEWAY_SECRET_ARN"
      value = var.gateway_secret_arn
    }
  }

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.source.id}/${aws_s3_object.source.key}"
    buildspec = "buildspec.agent.yml"
  }
}

# --- Weekly trigger (EventBridge Scheduler -> StartBuild) --------------------
resource "aws_iam_role" "scheduler" {
  name = "${var.project}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${var.project}-scheduler"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["codebuild:StartBuild"], Resource = aws_codebuild_project.patch.arn }]
  })
}

resource "aws_scheduler_schedule" "weekly_patch" {
  name = "${var.project}-weekly-patch"
  flexible_time_window { mode = "OFF" }
  schedule_expression          = "cron(0 3 ? * MON *)" # Mondays 03:00 UTC
  schedule_expression_timezone = "UTC"

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:codebuild:startBuild"
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ ProjectName = aws_codebuild_project.patch.name })
  }
}
