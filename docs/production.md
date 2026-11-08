# Productionizing and scaling

## What changes before production

- **Accounts and tenancy:** sign-in (OIDC), repositories owned by an organisation, and row-level
  security in Postgres. Today the API and MCP endpoint have no authentication.
- **Shared file storage:** the api and worker share a Docker volume (`DATA_DIR`) for uploads, clones
  and graph output. In production that moves to object storage.
- **Durable jobs:** arq on Redis loses queued jobs if Redis restarts. A managed queue (SQS, Cloud
  Tasks, Service Bus) with retries and a dead-letter queue fixes that.
- **Schema migrations:** today the schema is created with `CREATE ... IF NOT EXISTS` on startup.
  Production needs a migration tool (Alembic).
- **Limits:** rate limits per user, a daily spend cap per organisation (the answer log already
  records cost), and request size limits.
- **Index consistency:** re-indexing replaces rows in place, so a search during a re-index can see a
  partial index. Writing each commit to its own snapshot and switching atomically fixes that.
- **Freshness:** index only the files changed between commits, triggered by a GitHub push webhook.
  The embedding cache already makes this cheap.
- **Security:**
  - Scan repositories for secrets before indexing, and keep `.env`-style files out of the index.
  - Screen tool output for prompt injection.
  - Keep keys in a secrets manager.
- **Long conversations:** summarise or trim old turns before the context grows too large.
- **Delivery:** CI (tests, lint, type checks, image build and scan), infrastructure as code, and
  staged deploys.

## Reference deployment on AWS

| Piece | AWS service | Notes |
|---|---|---|
| api + MCP | ECS Fargate behind an Application Load Balancer | Stateless; scale on CPU and open connections. Raise the ALB idle timeout so streamed answers aren't cut. |
| worker | ECS Fargate service | Scale on queue depth. Parsing and graphify are CPU-bound. |
| web | Next.js standalone on ECS or App Runner, behind CloudFront | |
| Database | Aurora PostgreSQL with pgvector, Multi-AZ | Read replicas for search; partition large tables by repository. |
| Queue | SQS (or ElastiCache Redis to keep arq) | |
| Files | S3 | Uploads, clone cache, graph output |
| LLM | Claude on Amazon Bedrock | IAM auth, billing and data in the AWS account. The Anthropic API also works. |
| Embeddings | Voyage API | Or an embedding model hosted in the account, if data must not leave it; re-run the eval to compare. |
| Secrets | Secrets Manager | |
| Logs and traces | CloudWatch Logs; AWS Distro for OpenTelemetry to X-Ray | The app already emits JSON logs and OTLP traces. |
| Edge and network | WAF, private subnets, VPC endpoints, KMS | |
| Delivery | GitHub Actions to ECR, then ECS; Terraform or CDK | |

## Other clouds

| Piece | GCP | Azure | Cloudflare |
|---|---|---|---|
| api, worker | Cloud Run (services; jobs for indexing) | Container Apps | Containers (the Python worker and graphify can't run in Workers) |
| Postgres + pgvector | Cloud SQL or AlloyDB | Azure Database for PostgreSQL | An external managed Postgres (Neon, Supabase) through Hyperdrive |
| Queue | Cloud Tasks or Pub/Sub | Service Bus | Queues |
| Files | Cloud Storage | Blob Storage | R2 |
| Claude | Vertex AI | Microsoft Foundry | Anthropic API, through AI Gateway for caching and limits |
| Traces | Cloud Trace | Application Insights | Any OTLP backend |

On Cloudflare, Vectorize would replace pgvector, but it would split hybrid search across two systems.
Keeping Postgres is simpler.

## Scaling past one large repository

- **More repositories:** add workers. Indexing is per repository, and the api is stateless.
- **Larger repositories (monorepos):**
  - `grep` scans stored files in Python; use a trigram index (zoekt) or ripgrep over the checkout.
  - Move vectors to a dedicated store once a single index reaches tens of millions of chunks.
  - Move keyword search to a BM25 engine (ParadeDB `pg_search`, Tantivy).
- **Precision:** SCIP indexes (compiler-grade definitions and references) would make `related` and
  `impact` exact, including calls into dependencies.
- **Operations:**
  - Metrics and dashboards (OpenTelemetry metrics into Prometheus/Grafana).
  - Error tracking (Sentry).
  - Alerts on error rate, latency and daily spend.
  - Retention for the answer log, and a persistent trace store.

---

[README](../README.md#documentation) · Previous: [Key technical decisions](decisions.md) · Next: [Engineering standards](engineering-standards.md)
