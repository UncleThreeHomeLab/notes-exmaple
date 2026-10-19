terraform {
  required_version = "= 1.13.1"
  required_providers {
    github = { source = "integrations/github", version = "= 6.13.0" }
  }
  backend "http" {}
  encryption {
    key_provider "pbkdf2" "recovery" { passphrase = var.state_passphrase }
    method "aes_gcm" "protected" { keys = key_provider.pbkdf2.recovery }
    state {
      method   = method.aes_gcm.protected
      enforced = true
    }
    plan {
      method   = method.aes_gcm.protected
      enforced = true
    }
  }
}
variable "state_passphrase" {
  type      = string
  sensitive = true
  validation {
    condition     = length(var.state_passphrase) >= 32
    error_message = "Supply the durable vault recovery passphrase."
  }
}
provider "github" { owner = "UncleThreeHomeLab" }
resource "github_repository" "notes" {
  name                   = "notes-exmaple"
  description            = "Notes application with local ARC CI and GitOps releases"
  visibility             = "public"
  auto_init              = true
  has_issues             = true
  has_projects           = false
  has_wiki               = false
  allow_merge_commit     = false
  allow_squash_merge     = true
  allow_rebase_merge     = false
  delete_branch_on_merge = true
  lifecycle { prevent_destroy = true }
}
resource "github_branch_default" "main" {
  repository = github_repository.notes.name
  branch     = "main"
  lifecycle { prevent_destroy = true }
}
resource "github_repository_ruleset" "main" {
  name        = "protected-main"
  repository  = github_repository.notes.name
  target      = "branch"
  enforcement = "active"
  conditions {
    ref_name {
      include = ["refs/heads/main"]
      exclude = []
    }
  }
  rules {
    deletion                = true
    non_fast_forward        = true
    required_linear_history = true
    pull_request {
      allowed_merge_methods             = ["squash"]
      dismiss_stale_reviews_on_push     = true
      required_approving_review_count   = 0
      required_review_thread_resolution = true
    }
    required_status_checks {
      strict_required_status_checks_policy = true
      required_check {
        context        = "test"
        integration_id = 15368
      }
    }
  }
  lifecycle { prevent_destroy = true }
}
