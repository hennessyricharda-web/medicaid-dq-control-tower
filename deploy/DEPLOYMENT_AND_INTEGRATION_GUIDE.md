# Medicaid Data Quality (DQ) Agent & Command Tower
## Production Deployment & Integration Guide (AWS, Azure, and Enterprise Data Pipelines)

---

## 1. System Architecture & Topology

The Medicaid Data Quality (DQ) Agent and Command Tower provide an enterprise-grade, human-in-the-loop (HITL) quality control plane for state Medicaid, CHIP, and HHS medallion data estates (Claims, Encounters, Eligibility, Enrollment, Prior Auth, Pharmacy, and Provider networks).

```
                      +------------------------------------------+
                      |         Command Tower Frontend           |
                      |     Next.js 15 + React Flow UI (:3000)   |
                      +---------------------+--------------------+
                                            | REST / OIDC
                                            v
                      +------------------------------------------+
                      |          DQ Agent FastAPI Backend        |
                      |      Runtime, Triage & Gov Engine (:8000)|
                      +-----------+------------------+-----------+
                                  |                  |
           +----------------------+                  +-----------------------+
           | SQL / Advisory Locks                    | Ingestion & Gating    |
           v                                         v                       v
+-----------------------+                 +---------------------+  +--------------------+
| Control Plane DB      |                 | Data Plane Engine   |  | LLM Explainer      |
| PostgreSQL 15+        |                 | DuckDB / Snowflake  |  | Anthropic / Cortex |
| (Audit, State, Rules) |                 | Databricks / Synapse|  | Azure OpenAI / Bed |
+-----------------------+                 +---------------------+  +--------------------+
```

### Components:
1. **Control Plane (`dqagent.cp`)**: Backed by PostgreSQL (`dq_ctl` schema) storing immutable audit hash chains, rule registry, change requests, human-in-the-loop decisions, and pipeline locks.
2. **Backend Engine (`dqagent`)**: FastAPI runtime executing deterministic ABC (Audit, Balance, Control) rules, defect clustering, quarantine management, and LLM triage explainer.
3. **Command Tower (`tower/`)**: Next.js 15 web interface featuring live interactive DAGs (`@xyflow/react`), trace evidence drawers, step-up passkey re-authentication, and dual-person governance approval.
4. **Data Plane**: Pluggable storage adapter evaluating data at raw, bronze, silver, and gold tiers.

---

## 2. Deployment on Amazon Web Services (AWS)

### 2.1 Target Infrastructure
- **Control Plane**: Amazon RDS for PostgreSQL (Multi-AZ, PostgreSQL 15 or 16).
- **Compute Layer**: AWS ECS (Elastic Container Service) on Fargate or AWS EKS.
- **Storage / Data Plane**: Amazon S3 (Medallion buckets) + AWS Glue / Athena / Snowflake.
- **Ingestion Orchestration**: Amazon MWAA (Managed Workflows for Apache Airflow) or AWS Step Functions.

### 2.2 Environment Variables (AWS ECS / EKS)

```bash
# PostgreSQL Control Plane (RDS)
DQ_PG_HOST=dq-control-plane.xxxxxx.us-east-1.rds.amazonaws.com
DQ_PG_PORT=5432
DQ_PG_USER=dq_gov
DQ_PG_PASSWORD=<SecretsManager-Retrieved-Password>
DQ_PG_DATABASE=postgres

# Deployment Mode & Security
DQ_DEPLOYMENT=production
DQ_ALLOW_IN_MEMORY_DP=false

# OIDC / AWS Cognito Authentication
DQ_OIDC_ISSUER=https://cognito-idp.us-east-1.amazonaws.com/<userpool-id>
DQ_OIDC_AUDIENCE=<app-client-id>

# LLM Explainer (Amazon Bedrock or Anthropic)
DQ_LLM_PROVIDER=anthropic
DQ_LLM_TRANSPORT=live
ANTHROPIC_API_KEY=<SecretsManager-Retrieved-Key>
# Or AWS Bedrock configuration:
# AWS_REGION=us-east-1
# DQ_LLM_BEDROCK_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0

# Alert Sinks (Slack / EventBridge / Webhook)
DQ_ALERT_SINK=webhook
DQ_ALERT_WEBHOOK_URL=https://events.internal.domain/dq-alerts
```

### 2.3 AWS ECS Deployment Steps
1. **Build and push Docker images to Amazon ECR**:
   ```bash
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <aws_account_id>.dkr.ecr.us-east-1.amazonaws.com
   
   # Build & push Backend
   docker build -t medicaid-dq-backend -f deploy/Dockerfile.backend .
   docker tag medicaid-dq-backend:latest <aws_account_id>.dkr.ecr.us-east-1.amazonaws.com/medicaid-dq-backend:latest
   docker push <aws_account_id>.dkr.ecr.us-east-1.amazonaws.com/medicaid-dq-backend:latest

   # Build & push Tower
   docker build -t medicaid-dq-tower -f deploy/Dockerfile.tower .
   docker tag medicaid-dq-tower:latest <aws_account_id>.dkr.ecr.us-east-1.amazonaws.com/medicaid-dq-tower:latest
   docker push <aws_account_id>.dkr.ecr.us-east-1.amazonaws.com/medicaid-dq-tower:latest
   ```

2. **Run Schema Migration Task on RDS**:
   ```bash
   aws ecs run-task --cluster dq-cluster --task-definition dq-schema-migrate
   ```

---

## 3. Deployment on Microsoft Azure

### 3.1 Target Infrastructure
- **Control Plane**: Azure Database for PostgreSQL Flexible Server.
- **Compute Layer**: Azure Container Apps (ACA) or Azure Kubernetes Service (AKS).
- **Storage / Data Plane**: Azure Data Lake Storage Gen2 (ADLS Gen2) + Azure Databricks / Azure Synapse / Microsoft Fabric.
- **Ingestion Orchestration**: Azure Data Factory (ADF) or Managed Airflow in ADF.

### 3.2 Environment Variables (Azure Container Apps / AKS)

```bash
# Azure Database for PostgreSQL Flexible Server
DQ_PG_HOST=dq-psql-flex.postgres.database.azure.com
DQ_PG_PORT=5432
DQ_PG_USER=dq_gov
DQ_PG_PASSWORD=<KeyVault-Secret-Reference>
DQ_PG_DATABASE=postgres

# Runtime Mode
DQ_DEPLOYMENT=production
DQ_ALLOW_IN_MEMORY_DP=false

# Microsoft Entra ID (OIDC)
DQ_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
DQ_OIDC_AUDIENCE=<api-registration-client-id>

# Azure OpenAI / Anthropic Explainer
DQ_LLM_PROVIDER=anthropic
AZURE_OPENAI_ENDPOINT=https://<your-instance>.openai.azure.com/
AZURE_OPENAI_API_KEY=<KeyVault-Secret-Reference>

# Frontend Endpoint
NEXT_PUBLIC_API_URL=https://dq-api.internal.<region>.azurecontainerapps.io
```

### 3.3 Azure CLI Deployment Script

```bash
# 1. Login & set resource group
az login
az group create --name rg-medicaid-dq --location eastus

# 2. Create PostgreSQL Flexible Server
az postgres flexible-server create \
  --resource-group rg-medicaid-dq \
  --name dq-psql-flex \
  --admin-user dq_gov \
  --admin-password "<YourSecurePassword>" \
  --sku-name Standard_D2s_v3 \
  --version 16

# 3. Deploy Azure Container App Environment
az containerapp env create \
  --name env-medicaid-dq \
  --resource-group rg-medicaid-dq \
  --location eastus

# 4. Deploy Backend Container App
az containerapp create \
  --name dq-backend \
  --resource-group rg-medicaid-dq \
  --environment env-medicaid-dq \
  --image <acr_name>.azurecr.io/medicaid-dq-backend:latest \
  --target-port 8000 \
  --ingress internal \
  --env-vars DQ_PG_HOST=dq-psql-flex.postgres.database.azure.com DQ_PG_USER=dq_gov DQ_DEPLOYMENT=production

# 5. Deploy Command Tower UI Container App
az containerapp create \
  --name dq-command-tower \
  --resource-group rg-medicaid-dq \
  --environment env-medicaid-dq \
  --image <acr_name>.azurecr.io/medicaid-dq-tower:latest \
  --target-port 3000 \
  --ingress external \
  --env-vars NEXT_PUBLIC_API_URL=https://dq-backend.internal.env-medicaid-dq.eastus.azurecontainerapps.io
```

---

## 4. Pipeline Integration & Checkpoint Enforcement

The system implements the **Four-Checkpoint Architecture** protecting data as it moves from Raw to Gold:

```
[Raw Ingestion]
      |
[Checkpoint 1: Contract Intake] ------------> Refuse malformed batch
      v
   [Bronze]
      |
[Checkpoint 2: Validity Checks] ------------> Divert defect rows to Quarantine Table
      v
   [Silver]
      |
[Checkpoint 3: Volume & Recon] -------------> Trigger HOLD on volume anomalies (e.g. -41%)
      v
    [Gold]
      |
[Checkpoint 4: Publish Gate] ---------------> Emit only verified rows to *_RELEASED views
```

### 4.1 Apache Airflow / Cloud Composer Integration
Use the included operator (`deploy/airflow_dq_operator.py`):

```python
from airflow import DAG
from datetime import datetime
from deploy.airflow_dq_operator import MedicaidDQGateOperator

with DAG(
    dag_id="medicaid_claims_nightly_pipeline",
    start_date=datetime(2026, 1, 1),
    schedule_interval="@daily",
    catchup=False,
) as dag:

    dq_gate_silver = MedicaidDQGateOperator(
        task_id="dq_gate_silver_checkpoint",
        dq_api_url="http://dq-backend.internal:8000",
        pipeline_id="maternal-health-nightly",
        batch_id="{{ ds_nodash }}",
        node_id="silver",
        fail_closed=True,
    )
    
    # Dependencies
    # ingest_bronze >> dq_gate_silver >> publish_gold
```

### 4.2 Databricks, Spark & dbt Integration
Run post-hook gating (`deploy/databricks_dbt_hook.py`) in your pipeline:

```bash
python deploy/databricks_dbt_hook.py silver
```

In `dbt_project.yml` post-hooks:
```yaml
models:
  medicaid:
    silver:
      +post-hook: "python deploy/databricks_dbt_hook.py silver"
```

### 4.3 Secure `_RELEASED` Consumer Pattern
Consumer reporting tools (Power BI, Tableau, analytics marts) must **never** query raw or silver tables directly. They query view equivalents that enforce the publish gate:

```sql
-- Pattern for downstream consumer view
CREATE OR REPLACE VIEW GOLD_MEDICAID.MATERNAL_EPISODES_RELEASED AS
SELECT m.*
FROM GOLD_MEDICAID.MATERNAL_EPISODES m
INNER JOIN DQ_CTL.RELEASED_UNITS r
  ON r.pipeline_id = 'maternal-health-nightly'
 AND r.batch_id = m.batch_id
 AND r.status = 'RELEASED';
```

---

## 5. Custom Rules & Overlay Configuration

1. **Federal T-MSIS Baseline Rules**: Managed in `rules/` (Claims, Eligibility, Providers, Encounter reconciliation).
2. **State Specific Overlays**: Add your state rules under `config/state/<STATE_CODE>/rules.yaml`:
   ```yaml
   state: STATE_A
   pipeline: maternal-health-nightly
   threshold_overrides:
     DQ-SAMPLE-ENC-02:
       max_variance_percent: 15.0
       action_on_breach: HOLD
     DQ-SAMPLE-IP-07:
       action_on_breach: QUARANTINE
   ```

---

## 6. Local Quickstart & Verification

```bash
# 1. Start all services locally with docker-compose
docker-compose -f deploy/docker-compose.yml up --build -d

# 2. Access Command Tower
# Open browser at: http://localhost:3000

# 3. Access FastAPI Swagger Documentation
# Open browser at: http://localhost:8000/docs

# 4. Run test suites
pytest tests/t0 tests/t1 -v
```
