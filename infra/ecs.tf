resource "aws_ecs_cluster" "this" {
  name = var.project
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.project}/app"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/ecs/${var.project}/collector"
  retention_in_days = 7
}

locals {
  app_image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.project
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = local.app_image
      essential = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment = [
        { name = "AWS_REGION", value = var.region },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        # App -> collector sidecar over the task's loopback interface.
        { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = "http://localhost:4318" },
        { name = "OTEL_EXPORTER_OTLP_PROTOCOL", value = "http/protobuf" },
        { name = "OTEL_SERVICE_NAME", value = var.project },
        { name = "DEPLOYMENT_ENV", value = "aws" },
        # Turn on the experimental Bedrock GenAI semconv: token usage, model,
        # and prompt/response on the spans. Without this only rpc.* is emitted.
        { name = "OTEL_SEMCONV_STABILITY_OPT_IN", value = "gen_ai_latest_experimental" },
        { name = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", value = "true" },
      ]
      dependsOn = [{ containerName = "collector", condition = "START" }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "app"
        }
      }
    },
    {
      name      = "collector"
      image     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
      essential = true
      environment = [
        { name = "BRONTO_OTLP_BASE", value = var.bronto_otlp_base },
      ]
      secrets = [
        # ADOT loads its YAML config from this env var.
        { name = "AOT_CONFIG_CONTENT", valueFrom = aws_ssm_parameter.collector_config.arn },
        { name = "BRONTO_API_KEY", valueFrom = aws_secretsmanager_secret.bronto_api_key.arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.collector.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "collector"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "app" {
  name            = var.project
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}
