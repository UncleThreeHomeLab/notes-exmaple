# Notes example

Notes frontend and API, packaged as one non-root image. The API uses application-scoped PostgreSQL and S3 credentials; browser authentication uses OIDC. No production credentials are included.

Run tests with `docker compose run --build --rm test`. The image starts the frontend by default; start the API with `python api.py`. Configure OIDC and mount database/object credentials through the deployment repository.

Actions jobs use local ephemeral ARC runners. Untrusted fork workflows do not execute. Protected `main` requires the `test` check; publishing follows a reviewed merge. Deployment changes go through a separate GitOps release PR.

Repository governance uses OpenTofu 1.13.1 and GitHub provider 6.13.0 in `.cloudlab/github`. Supply local `GITHUB_TOKEN`, `TF_VAR_state_passphrase`, `TF_HTTP_ADDRESS`, `TF_HTTP_USERNAME` and `TF_HTTP_PASSWORD`; use the designated encrypted HTTP backend. Run `docker compose run --rm governance init -lockfile=readonly`, then `docker compose run --rm governance plan` and `docker compose run --rm governance apply`. Governance credentials never enter Actions or Kubernetes.
