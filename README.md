# cloudflare-kubernetes-pools
This application discover Kubernetes Ingresses and add them to Cloudflare pools to have DNS geo-routing

## Configuration

The application uses environment variables for configuration:

### Required Environment Variables
- `CF_API_TOKEN`: Cloudflare API token with Load Balancing: Monitors and Pools Write permissions
- `CF_ACCOUNT_ID`: Your Cloudflare account ID
- `CF_ZONE_ID`: Your Cloudflare zone ID where the load balancer will be created

### Optional Environment Variables
- `LABEL_SELECTOR`: Label selector to watch for Ingresses (default: `watch=true`)
- `CF_POOL_NAME`: Name for the Cloudflare pool (default: `k8s-ingress-pool`)
- `CF_LB_NAME`: Name for the Cloudflare load balancer (default: `k8s-ingress-lb`) - **Note: This is now used for reference only**
- `CF_LB_HOSTNAME`: Hostname for the load balancer (default: `app.example.com`) - **This is the actual DNS hostname that will be created**
- `CF_POOL_LATITUDE`: Latitude of the data center containing the origins (decimal degrees)
- `CF_POOL_LONGITUDE`: Longitude of the data center containing the origins (decimal degrees)
- `CF_ORIGIN_WEIGHT`: Weight for the origin server in load balancing (default: `33`)

### Geographic Configuration
To enable geographic load balancing, set both `CF_POOL_LATITUDE` and `CF_POOL_LONGITUDE` environment variables. Both values must be provided together as decimal degrees.

Example:
```bash
CF_POOL_LATITUDE=37.7749
CF_POOL_LONGITUDE=-122.4194
```

If only one coordinate is provided, geographic configuration will be skipped with a warning.

### Load Balancer Configuration
The application now uses the zone-level Cloudflare API endpoints and creates load balancers with the following configuration:
- **Steering Policy**: `least_connections` - Routes traffic to the origin with the fewest active connections
- **Proxied**: `true` - Traffic is proxied through Cloudflare (orange cloud)
- **TTL**: `30` seconds for DNS resolution

## Usage

The application watches for Kubernetes Ingresses with the specified label selector and automatically creates or updates Cloudflare pools and load balancers based on the ingress load balancer IP addresses. The load balancer will be created with the hostname specified in `CF_LB_HOSTNAME`.
