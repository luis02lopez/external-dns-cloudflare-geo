# External DNS Cloudflare Geo

ExternalDNS-like support for Cloudflare geo-routed load balancing policies. This application watches Kubernetes Ingresses and automatically creates or updates geo-routed load balancers in Cloudflare.

## Features

- 🌍 **Geo-routing**: Automatically creates geo-routed load balancers based on ingress load balancer IPs
- 🔄 **Multi-cluster coordination**: Intelligent merging of origins across multiple clusters
- 🔄 **Real-time updates**: Watches Kubernetes ingresses for changes and updates Cloudflare resources accordingly
- 🌐 **Multi-cluster support**: Intelligent merging of origins across multiple clusters with geo-location identification
- 🔌 **Robust API handling**: Uses Cloudflare API with retry logic and proper error handling
- 🔄 **Auto-reconnection**: Automatic reconnection for watch streams to prevent pod restarts
- 🛡️ **Robust error handling**: Comprehensive error handling with retry logic and proper logging
- 📊 **Production ready**: Includes health checks, structured logging, and security best practices
- 🔒 **Security**: Runs as non-root user with minimal privileges

## Architecture

This application is designed to work across multiple Kubernetes clusters to provide geo-distributed load balancing:

- **Multi-cluster deployment**: Each cluster runs its own instance with a unique `GEO_LOCATION` environment variable
- **Intelligent merging**: When updating pools, the application preserves origins from other clusters
- **Cloudflare Load Balancing**: Uses Cloudflare's Load Balancing service with proxy benefits
- **Automatic recovery**: Watch streams automatically reconnect to prevent service disruption

### Multi-Cluster Flow

1. Each cluster's external-dns-cloudflare-geo instance watches local ingresses
2. When an ingress gets a load balancer IP, the instance uses the `GEO_LOCATION` environment variable
3. The application merges its origin with existing ones from other clusters
4. Result: A single Cloudflare load balancer with multiple origins from different geo-locations

## Configuration

The application uses environment variables for configuration and extracts cluster information from ingress labels.

### Required Environment Variables
- `CF_API_TOKEN`: Cloudflare API token with Load Balancing: Monitors and Pools Write permissions
- `CF_ACCOUNT_ID`: Your Cloudflare account ID
- `CF_ZONE_ID`: Your Cloudflare zone ID where the load balancer will be created
- `GEO_LOCATION`: Geo-location identifier for this cluster - **Important for multi-cluster coordination**

### Optional Environment Variables
- `LABEL_SELECTOR`: Label selector to watch for Ingresses (default: `dns.external/geo-route=true`)
- `CF_LB_HOSTNAME`: Hostname for the load balancer (default: `app.example.com`) - **This is the actual DNS hostname that will be created**
- `CF_ORIGIN_WEIGHT`: Weight for the origin server in load balancing (default: `33`, range: 1-100)

### Required Ingress Labels

The application extracts the following information from ingress labels:

| Label | Required | Description | Example |
|-------|----------|-------------|---------|
| `cluster-name` or `cluster_name` | Yes | Cluster identifier | `prod-cluster-1`, `staging-eu` |

### Supported Geo-Locations

The application supports three predefined geo-locations with automatic coordinate assignment:

| Location | Code | Latitude | Longitude | Description |
|----------|------|----------|-----------|-------------|
| **Europe** | `eu` | 50.1109 | 8.6821 | Frankfurt, Germany |
| **United States** | `us` | 37.7749 | -122.4194 | San Francisco, CA |
| **Asia** | `asia` | 35.6762 | 139.6503 | Tokyo, Japan |

### Pool Naming Convention

Pools are automatically named using the cluster name:
```
k8s-pool-{cluster-name}
```

For example:
- `k8s-pool-prod-cluster-1`
- `k8s-pool-staging-eu`
- `k8s-pool-dev-us`

### Load Balancer Configuration
The application creates Cloudflare load balancers with the following configuration:
- **Steering Policy**: `least_connections` - Routes traffic to the origin with the fewest active connections
- **Proxied**: `true` - Traffic is proxied through Cloudflare (orange cloud)
- **TTL**: `30` seconds for DNS resolution

## Kubernetes Deployment

To deploy this application in Kubernetes, you'll need to create the following objects:

### 1. Namespace (Optional)
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: external-dns-cloudflare-geo
```

### 2. ServiceAccount
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: external-dns-cloudflare-geo-sa
  namespace: external-dns-cloudflare-geo
```

### 3. ClusterRole
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: external-dns-cloudflare-geo-role
rules:
- apiGroups: ["networking.k8s.io"]
  resources: ["ingresses"]
  verbs: ["get", "list", "watch"]
```

### 4. ClusterRoleBinding
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: external-dns-cloudflare-geo-binding
subjects:
- kind: ServiceAccount
  name: external-dns-cloudflare-geo-sa
  namespace: external-dns-cloudflare-geo
roleRef:
  kind: ClusterRole
  name: external-dns-cloudflare-geo-role
  apiGroup: rbac.authorization.k8s.io
```

### 5. Secret for Cloudflare API Token
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: cloudflare-api-secret
  namespace: external-dns-cloudflare-geo
type: Opaque
data:
  CF_API_TOKEN: <base64-encoded-api-token>
```

### 6. ConfigMap for Configuration
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: external-dns-cloudflare-geo-config
  namespace: external-dns-cloudflare-geo
data:
  CF_ACCOUNT_ID: "your-cloudflare-account-id"
  CF_ZONE_ID: "your-cloudflare-zone-id"
  CF_LB_HOSTNAME: "app.example.com"
  CF_ORIGIN_WEIGHT: "33"
  LABEL_SELECTOR: "dns.external/geo-route=true"
  GEO_LOCATION: "eu"
```

### 7. Deployment
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: external-dns-cloudflare-geo
  namespace: external-dns-cloudflare-geo
spec:
  replicas: 1
  selector:
    matchLabels:
      app: external-dns-cloudflare-geo
  template:
    metadata:
      labels:
        app: external-dns-cloudflare-geo
    spec:
      serviceAccountName: external-dns-cloudflare-geo-sa
      containers:
      - name: external-dns-cloudflare-geo
        image: ghcr.io/your-org/external-dns-cloudflare-geo:latest
        envFrom:
        - configMapRef:
            name: external-dns-cloudflare-geo-config
        env:
        - name: CF_API_TOKEN
          valueFrom:
            secretKeyRef:
              name: cloudflare-api-secret
              key: CF_API_TOKEN
        resources:
          requests:
            memory: "64Mi"
            cpu: "50m"
          limits:
            memory: "128Mi"
            cpu: "100m"
        securityContext:
          allowPrivilegeEscalation: false
          runAsNonRoot: true
          runAsUser: 1000
          capabilities:
            drop:
            - ALL
```

### 8. Example Ingress to Watch
To test the application, create an Ingress with the required labels:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: example-ingress
  namespace: default
  labels:
    dns.external/geo-route: "true"  # This label will be watched by the application
    cluster-name: "prod-cluster-1"  # Required: Cluster identifier
spec:
  ingressClassName: nginx
  rules:
  - host: app.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: example-service
            port:
              number: 80
```

## Usage

### Single Cluster Setup
For a single cluster deployment, simply deploy the application with a geo-location:

```yaml
env:
- name: GEO_LOCATION
  value: "us"
```

### Multiple Clusters
For multi-cluster setups, deploy the application in each cluster with different `GEO_LOCATION` values:

**Cluster 1 (Europe):**
```yaml
env:
- name: GEO_LOCATION
  value: "eu"
```

**Cluster 2 (United States):**
```yaml
env:
- name: GEO_LOCATION
  value: "us"
```

**Cluster 3 (Asia):**
```yaml
env:
- name: GEO_LOCATION
  value: "asia"
```

**Important**: All instances can start in any order and will intelligently merge their origins. When an instance updates the Cloudflare pool, it:

1. Retrieves the current pool (if it exists)
2. Preserves all existing origins from other clusters
3. Updates only its own origin with the new IP
4. Saves the merged pool back to Cloudflare

This ensures that Cloudflare pools remain consistent across all clusters, even if clusters are restarted or updated independently.

## Monitoring

### Logs

The application uses structured logging with the following levels:

- `INFO`: Normal operations, ingress events, pool updates, origin merging
- `WARNING`: Recoverable errors, watch stream timeouts, API retries, missing labels
- `ERROR`: Critical errors, failed pool updates, authentication failures
- `DEBUG`: Detailed debugging information, missing load balancer IPs

Key log messages to monitor:

- `Successfully created/updated pool` - Pool update success
- `Merging IP from geo-location with N existing origins` - Multi-cluster coordination
- `Starting/Restarting watch stream` - Stream reconnection events
- `Load Balancer IP detected` - Ingress processing
- `Processing for geo-location: X, cluster: Y` - Label extraction success
- `No cluster-name found in labels` - Missing cluster-name label

### Health Check

The container includes a health check endpoint accessible at the container level.

### Metrics

Consider adding Prometheus metrics for:
- Pool update success/failure rates
- Ingress processing times
- API call latencies
- Number of origins per pool

## Troubleshooting

### Common Issues

1. **Missing environment variables**
   ```
   ERROR - Missing required environment variables: ['CF_API_TOKEN', 'GEO_LOCATION']
   ```
   Solution: Ensure all required environment variables are set

2. **Invalid geo-location**
   ```
   ERROR - Invalid GEO_LOCATION 'invalid'. Must be one of: ['eu', 'us', 'asia']
   ```
   Solution: Use one of the supported geo-locations: `eu`, `us`, or `asia`

3. **Missing cluster-name label**
   ```
   WARNING - No cluster-name found in labels for default/api-ingress
   ```
   Solution: Add `cluster-name: "your-cluster-name"` to your ingress labels

4. **Cloudflare authentication failed**
   ```
   ERROR - Cloudflare API request failed: 401 Unauthorized
   ```
   Solution: Check API token permissions and ensure it has Load Balancing: Monitors and Pools Write permissions

5. **Zone not found**
   ```
   ERROR - Cloudflare API request failed: 404 Not Found
   ```
   Solution: Verify zone exists and API token has access to it

6. **No load balancer IP**
   ```
   DEBUG - No Load Balancer IP available for default/api-ingress
   ```
   Solution: Wait for ingress controller to assign IP or check ingress configuration

7. **Watch stream timeouts**
   ```
   WARNING - Watch stream ended: TimeoutError
   INFO - Reconnecting in 5 seconds...
   ```
   Solution: This is normal behavior. The application automatically reconnects every 5 minutes or when the stream fails

8. **Origin conflicts**
   ```
   INFO - Merging IP from geo-location with 2 existing origins
   ```
   Solution: This is expected behavior when multiple clusters update the same pool. Each cluster manages its own origin.

9. **Invalid weight value**
   ```
   ERROR - Invalid CF_ORIGIN_WEIGHT value: must be between 1 and 100
   ```
   Solution: Ensure CF_ORIGIN_WEIGHT is between 1 and 100

## Implementation Details

### Label Extraction

The application extracts information from ingress labels:

1. **Cluster name**: Looks for `cluster-name` or `cluster_name` labels
2. **Validation**: Validates that cluster-name is present

### Multi-Cluster Coordination

The application uses a sophisticated coordination mechanism:

1. **Origin Naming**: Each origin is named with the geo-location (`origin-{geo-location}`)
2. **Pool Naming**: Each pool is named with the cluster (`k8s-pool-{cluster-name}`)
3. **Duplicate Detection**: Before adding a new origin, it checks if the IP already exists
4. **Merging Logic**: When updating a pool, it preserves all existing origins and adds the new one
5. **Conflict Resolution**: If multiple clusters have the same IP, only one origin is created

### Predefined Coordinates

The application includes predefined coordinates for each supported geo-location:

- **Europe (eu)**: Frankfurt, Germany (50.1109, 8.6821)
- **United States (us)**: San Francisco, CA (37.7749, -122.4194)
- **Asia (asia)**: Tokyo, Japan (35.6762, 139.6503)

These coordinates are automatically used for each geo-location.

### API Error Handling

The application includes robust error handling:

- **Retry Logic**: Automatic retries for transient failures (429, 500, 502, 503, 504)
- **Timeout Handling**: 30-second timeouts for all API calls
- **Session Management**: Persistent HTTP sessions with connection pooling
- **Error Logging**: Detailed error messages with context

### Security Features

- **Non-root Execution**: Application runs as non-root user (UID 1000)
- **Minimal Privileges**: Only requires ingress read permissions
- **Secure Headers**: Proper Content-Type and Authorization headers
- **Input Validation**: All environment variables are validated before use

## Dependencies

The application uses the following key Python packages:

- `kubernetes>=29.0.0`: For Kubernetes API access and watching ingresses
- `requests>=2.25.0`: For Cloudflare API calls with retry logic
- `urllib3>=1.26.0`: For HTTP connection pooling and retry strategies

See `requirements.txt` for complete dependency list.

## Related Projects

- [External DNS GCP Geo](https://github.com/magma-devs/external-dns-gcp-geo): Similar project for Google Cloud DNS
- [ExternalDNS](https://github.com/kubernetes-sigs/external-dns): The original ExternalDNS project

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
