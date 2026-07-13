# Collector-only ECS Fargate service. No app container here - the agent runs
# on AgentCore Runtime; this service just receives its OTLP/HTTP traffic (via
# the ALB in alb.tf) and fans it out to both Bronto accounts.

resource "aws_ecs_cluster" "collector" {
  name = "${var.project}-collector"
}

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/ecs/${var.project}/collector"
  retention_in_days = 7
}

resource "aws_ecs_task_definition" "collector" {
  family                   = "${var.project}-collector"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.collector_execution.arn

  container_definitions = jsonencode([
    {
      name      = "collector"
      image     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
      essential = true
      portMappings = [
        { containerPort = 4318, protocol = "tcp" },
        { containerPort = 13133, protocol = "tcp" },
      ]
      environment = [
        { name = "BRONTO_OTLP_BASE", value = var.bronto_otlp_base },
        { name = "BRONTO_OTLP_BASE_2", value = var.bronto_otlp_base_2 },
      ]
      secrets = [
        # ADOT loads its YAML config from this env var.
        { name = "AOT_CONFIG_CONTENT", valueFrom = aws_ssm_parameter.collector_config.arn },
        { name = "BRONTO_API_KEY", valueFrom = aws_secretsmanager_secret.bronto_api_key.arn },
        { name = "BRONTO_API_KEY_2", valueFrom = aws_secretsmanager_secret.bronto_api_key_2.arn },
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

resource "aws_ecs_service" "collector" {
  name            = "${var.project}-collector"
  cluster         = aws_ecs_cluster.collector.id
  task_definition = aws_ecs_task_definition.collector.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.collector.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.collector.arn
    container_name   = "collector"
    container_port   = 4318
  }

  depends_on = [aws_lb_listener.collector_http]
}
