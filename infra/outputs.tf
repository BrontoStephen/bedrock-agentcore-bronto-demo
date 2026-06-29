output "alb_url" {
  value       = "http://${aws_lb.app.dns_name}"
  description = "Open this in a browser to use the demo."
}

output "ecr_repository_url" {
  value       = aws_ecr_repository.app.repository_url
  description = "Push the app image here before deploying."
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_name" {
  value = aws_ecs_service.app.name
}

output "rebuild_project" {
  value       = aws_codebuild_project.rebuild.name
  description = "CodeBuild project run weekly (and on demand) to rebuild + redeploy with patches."
}
