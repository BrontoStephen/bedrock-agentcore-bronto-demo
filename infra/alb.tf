# Public ALB fronting the collector-only ECS service. The deployed AgentCore
# Runtime sends its OTLP/HTTP traffic here (http://<dns_name>) instead of
# straight to Bronto; the collector holds the Bronto credentials and fans out
# to both accounts (see ../collector/otel-collector-config.yaml).

resource "aws_lb" "collector" {
  # AWS caps ALB names at 32 chars; "-collector-alb" overflows var.project's
  # full name, so abbreviate.
  name               = "${var.project}-clr-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.collector_alb.id]
  subnets            = data.aws_subnets.default.ids
}

resource "aws_lb_target_group" "collector" {
  name        = "${var.project}-clr-tg"
  port        = 4318
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  # 4318 is the OTLP receiver, not a health endpoint - point the health check
  # at the ADOT collector's separate health_check extension port instead.
  health_check {
    port                = 13133
    path                = "/"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "collector_http" {
  load_balancer_arn = aws_lb.collector.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.collector.arn
  }
}
