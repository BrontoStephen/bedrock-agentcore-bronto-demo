# Use the account's default VPC + subnets to keep the demo simple (no NAT gateway).
# The collector's Fargate task gets a public IP so it can pull its image and
# reach Bronto; the ALB in front of it is public too, because the AgentCore
# Runtime that sends it OTLP runs in AWS's managed PUBLIC network mode with no
# fixed egress IP to allowlist (same trust model as the root demo's ALB).

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "collector_alb" {
  name        = "${var.project}-collector-alb"
  description = "Allow inbound HTTP to the collector ALB"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP (OTLP/HTTP from the AgentCore Runtime)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "collector" {
  name        = "${var.project}-collector"
  description = "Collector task: allow OTLP + health-check traffic from the ALB, all egress"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "OTLP/HTTP from ALB"
    from_port       = 4318
    to_port         = 4318
    protocol        = "tcp"
    security_groups = [aws_security_group.collector_alb.id]
  }

  ingress {
    description     = "ADOT health_check extension from ALB"
    from_port       = 13133
    to_port         = 13133
    protocol        = "tcp"
    security_groups = [aws_security_group.collector_alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
