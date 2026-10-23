# Notes example

Notes frontend and API, packaged as one non-root image. The API uses application-scoped PostgreSQL and S3 credentials; browser authentication uses OIDC. No production credentials are included.

Run application tests with `docker compose run --build --rm test` and workflow tests with `docker compose run --rm ci-test`. The image starts the frontend by default; start the API with `python api.py`. Configure OIDC and mount database/object credentials through the deployment repository.

Actions jobs use local ephemeral ARC runners. Untrusted fork workflows do not execute. Protected `main` requires the `test / test` check; publishing follows a reviewed merge. Deployment changes go through a separate GitOps release PR.
All CI commands live in `.github/workflows/ci.yaml`, with separate build, scan,
push, signature verification and release PR steps. Runner infrastructure supplies
pinned tools and scoped credentials; CI does not download platform workflows or scripts.

The frontend supports measured HTTPRoute canaries through the platform service
chart; the API retains its Deployment. Signed image changes still require the
separate GitOps release PR. `compose.release.yaml` uses an adjacent Cloudlab checkout
(`CLOUDLAB_DIR`), its ignored `.env` and existing Compose state volumes. Its
`release-proof` service accepts `prepare`, `inspect`, `migrate --digest sha256:…`,
`measure --case healthy|faulty|missing|low-samples|query-failure --digest sha256:…`
and `restored --digest sha256:…`. Start measurement before merging each release PR.
Traffic reads the actual Notes homepage through verified TLS at 6 RPS, four VUs,
190 seconds; its user agent identifies synthetic requests. The reviewed release
policy must match the platform. Negative analysis fixtures change only the pending
Rollouts-owned AnalysisRun. Restore the signed known-good Git release after aborts.
The checkpoint and results stay in the private recovery volume. `migrate` retires
only the old stateless frontend after its healthy Rollout owns stable traffic;
it preserves the API, application credentials and data.

Repository governance uses OpenTofu 1.13.1 and GitHub provider 6.13.0 in `.cloudlab/github`. Supply local `GITHUB_TOKEN`, `TF_VAR_state_passphrase`, `TF_HTTP_ADDRESS`, `TF_HTTP_USERNAME` and `TF_HTTP_PASSWORD`; use the designated encrypted HTTP backend. Run `docker compose run --rm governance init -lockfile=readonly`, then `docker compose run --rm governance plan` and `docker compose run --rm governance apply`. Governance credentials never enter Actions or Kubernetes.
