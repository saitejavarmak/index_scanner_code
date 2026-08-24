# Service Catalog

The **team scan** feature (`scan_team` MCP tool / `/api/scan-team` endpoint) needs a
service catalog: a CSV that maps each of your services to its owning team, its
language, and which database it uses. This lets the scanner answer
*"scan every MongoDB service owned by team X"* without you listing repos by hand.

This file is **organization-specific**, so it is not shipped. Create your own from
the template below.

## Create your catalog

```bash
cp service_catalog/service_catalog.csv.example service_catalog/service_catalog.csv
# then fill in your real services
```

By default the app looks for `service_catalog/service_catalog.csv`. You can point
at any path per request via the `catalog_path` field, or by passing
`--catalog-path` on the CLI.

## Columns

| Column | Required | Description |
|--------|----------|-------------|
| `Namespace` | yes | Deployment namespace (Kubernetes namespace, Helm release, etc.) |
| `Team` | yes | Owning team. This is what you filter on when scanning by team. |
| `ServiceName` | yes | Service identifier. Used to derive the repo name. |
| `Language` | no | Primary language (`Java`, `Kotlin`, `Python`, `JavaScript`, ...) |
| `URI location if present` | no | Path to the Helm values file holding DB connection info |
| `Sub Team` | no | Optional sub-team for finer-grained grouping |
| `DB Service` | yes | Database type — `MongoDB`, `PostgreSQL`, etc. Routes the service to the correct scanner. |

## Repo name derivation

Service names often carry an org-specific prefix (e.g. `svc_`, `app-`). Set
`SERVICE_NAME_PREFIXES` in `ui/api/server.py` so those prefixes are stripped when
deriving the Bitbucket repo name:

```python
SERVICE_NAME_PREFIXES = ('svc_', 'app-')
```

Leave it as an empty tuple `()` if your service names already match your repo names.
