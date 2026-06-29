# ---------------------------------------------------------------------------
# Weekly automated rebuild + redeploy (security patching).
#
#   EventBridge Scheduler (weekly cron)
#     -> CodeBuild StartBuild
#         -> docker build --pull (latest patched base + apt upgrade)
#         -> push to ECR
#         -> ecs update-service --force-new-deployment
#
# Source is a snapshot of the repo zipped into S3. Re-running `terraform apply`
# re-uploads the snapshot whenever tracked files change.
# ---------------------------------------------------------------------------

data "aws_region" "current" {}

locals {
  ecr_registry = split("/", aws_ecr_repository.app.repository_url)[0]
}

# ---- Source snapshot in S3 ----
resource "aws_s3_bucket" "source" {
  bucket        = "${var.project}-source-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "source" {
  bucket                  = aws_s3_bucket.source.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Zip only what the image build needs (never .env, infra state, or .git).
data "archive_file" "source" {
  type        = "zip"
  output_path = "${path.module}/builds/source.zip"
  source_dir  = "${path.module}/.."
  excludes = [
    "infra",
    ".git",
    ".env",
    "builds",
    "README.md",
  ]
}

resource "aws_s3_object" "source" {
  bucket = aws_s3_bucket.source.id
  key    = "source.zip"
  source = data.archive_file.source.output_path
  etag   = data.archive_file.source.output_md5
}

# ---- CodeBuild role ----
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
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = ["${aws_s3_bucket.source.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = [aws_ecr_repository.app.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = "*"
      }
    ]
  })
}

# ---- CodeBuild project ----
resource "aws_codebuild_project" "rebuild" {
  name         = "${var.project}-weekly-rebuild"
  service_role = aws_iam_role.codebuild.arn

  artifacts { type = "NO_ARTIFACTS" }

  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = true # required to run docker build

    environment_variable {
      name  = "AWS_REGION"
      value = data.aws_region.current.name
    }
    environment_variable {
      name  = "ECR_REGISTRY"
      value = local.ecr_registry
    }
    environment_variable {
      name  = "ECR_REPO"
      value = aws_ecr_repository.app.repository_url
    }
    environment_variable {
      name  = "ECS_CLUSTER"
      value = aws_ecs_cluster.this.name
    }
    environment_variable {
      name  = "ECS_SERVICE"
      value = aws_ecs_service.app.name
    }
  }

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.source.id}/${aws_s3_object.source.key}"
    buildspec = "buildspec.yml"
  }

  logs_config {
    cloudwatch_logs {
      group_name = "/codebuild/${var.project}"
    }
  }
}

# ---- Weekly schedule (EventBridge Scheduler) ----
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
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["codebuild:StartBuild"]
      Resource = [aws_codebuild_project.rebuild.arn]
    }]
  })
}

resource "aws_scheduler_schedule" "weekly_rebuild" {
  name = "${var.project}-weekly-rebuild"

  flexible_time_window {
    mode = "OFF"
  }

  # Every Monday 03:00 UTC — ahead of the weekly account security scan.
  schedule_expression          = "cron(0 3 ? * MON *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:codebuild:startBuild"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      ProjectName = aws_codebuild_project.rebuild.name
    })
  }
}
