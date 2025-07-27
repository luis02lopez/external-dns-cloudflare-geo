import os
import logging
import sys
import json
import time
from typing import Optional, Dict, Any, List
from kubernetes import client, config, watch
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Predefined geo-location coordinates
GEO_LOCATIONS = {
    "eu": {"latitude": 50.1109, "longitude": 8.6821, "name": "Europe"},
    "us": {"latitude": 37.7749, "longitude": -122.4194, "name": "United States"},
    "asia": {"latitude": 35.6762, "longitude": 139.6503, "name": "Asia"}
}

# Environment variable validation
def validate_env_vars() -> Dict[str, str]:
    """Validate required environment variables."""
    required_vars = {
        'CF_API_TOKEN': os.getenv("CF_API_TOKEN"),
        'CF_ACCOUNT_ID': os.getenv("CF_ACCOUNT_ID"),
        'CF_ZONE_ID': os.getenv("CF_ZONE_ID"),
    }
    
    missing_vars = [var for var, value in required_vars.items() if not value]
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        sys.exit(1)
    
    # Validate optional numeric values
    try:
        origin_weight = int(os.getenv("CF_ORIGIN_WEIGHT", "33"))
        if origin_weight < 1 or origin_weight > 100:
            raise ValueError("CF_ORIGIN_WEIGHT must be between 1 and 100")
    except ValueError as e:
        logger.error(f"Invalid CF_ORIGIN_WEIGHT value: {e}")
        sys.exit(1)
    
    return {
        'CF_API_TOKEN': required_vars['CF_API_TOKEN'],
        'CF_ACCOUNT_ID': required_vars['CF_ACCOUNT_ID'],
        'CF_ZONE_ID': required_vars['CF_ZONE_ID'],
        'CF_LB_HOSTNAME': os.getenv("CF_LB_HOSTNAME", "app.example.com"),
        'CF_ORIGIN_WEIGHT': origin_weight,
        'LABEL_SELECTOR': os.getenv("LABEL_SELECTOR", "watch=true"),
    }

# Global configuration
try:
    CONFIG = validate_env_vars()
    CF_API_BASE = "https://api.cloudflare.com/client/v4"
    
    # Create session with retry strategy
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    headers = {
        "Authorization": f"Bearer {CONFIG['CF_API_TOKEN']}",
        "Content-Type": "application/json"
    }
    
except Exception as e:
    logger.error(f"Failed to initialize configuration: {e}")
    sys.exit(1)

def get_lb_ip(ingress: client.V1Ingress) -> Optional[str]:
    """Extract load balancer IP from ingress status."""
    try:
        if not ingress.status or not ingress.status.load_balancer:
            return None
        
        ingress_status = ingress.status.load_balancer.ingress
        if not ingress_status:
            return None
            
        lb_entry = ingress_status[0]
        return lb_entry.ip or lb_entry.hostname
    except (AttributeError, IndexError) as e:
        logger.warning(f"Failed to extract load balancer IP: {e}")
        return None

def extract_geo_location_from_labels(ingress: client.V1Ingress) -> Optional[str]:
    """Extract geo-location from ingress labels."""
    try:
        if not ingress.metadata or not ingress.metadata.labels:
            return None
        
        labels = ingress.metadata.labels
        
        # Check for geo-location in labels
        geo_location = labels.get('geo-location') or labels.get('geo_location')
        if geo_location and geo_location in GEO_LOCATIONS:
            return geo_location
        
        return None
    except Exception as e:
        logger.warning(f"Failed to extract geo-location from labels: {e}")
        return None

def extract_cluster_name_from_labels(ingress: client.V1Ingress) -> Optional[str]:
    """Extract cluster name from ingress labels."""
    try:
        if not ingress.metadata or not ingress.metadata.labels:
            return None
        
        labels = ingress.metadata.labels
        
        # Check for cluster name in labels
        cluster_name = labels.get('cluster-name') or labels.get('cluster_name')
        if cluster_name:
            return cluster_name
        
        return None
    except Exception as e:
        logger.warning(f"Failed to extract cluster name from labels: {e}")
        return None

def build_pool_name(cluster_name: str) -> str:
    """Build pool name from cluster name."""
    return f"k8s-pool-{cluster_name}"

def make_cloudflare_request(method: str, url: str, data: Dict = None) -> Optional[Dict]:
    """Make a Cloudflare API request with proper error handling."""
    try:
        if method.upper() == "GET":
            response = session.get(url, headers=headers, timeout=30)
        elif method.upper() == "POST":
            response = session.post(url, headers=headers, json=data, timeout=30)
        elif method.upper() == "PUT":
            response = session.put(url, headers=headers, json=data, timeout=30)
        elif method.upper() == "DELETE":
            response = session.delete(url, headers=headers, timeout=30)
        else:
            logger.error(f"Unsupported HTTP method: {method}")
            return None
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            logger.error(f"Cloudflare API request failed: {response.status_code} - {response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None

def find_pool_id_by_name(name: str) -> Optional[str]:
    """Find pool ID by name."""
    url = f"{CF_API_BASE}/accounts/{CONFIG['CF_ACCOUNT_ID']}/load_balancers/pools"
    result = make_cloudflare_request("GET", url)
    
    if result and result.get('success'):
        pools = result.get('result', [])
        for pool in pools:
            if pool['name'] == name:
                return pool['id']
    
    return None

def get_pool_origins(pool_id: str) -> List[Dict]:
    """Get current origins from a pool."""
    url = f"{CF_API_BASE}/accounts/{CONFIG['CF_ACCOUNT_ID']}/load_balancers/pools/{pool_id}"
    result = make_cloudflare_request("GET", url)
    
    if result and result.get('success'):
        return result['result'].get('origins', [])
    
    return []

def create_or_update_pool_with_coordination(ip_address: str, geo_location: str, cluster_name: str) -> Optional[str]:
    """Create or update pool with multi-cluster coordination."""
    try:
        pool_name = build_pool_name(cluster_name)
        pool_id = find_pool_id_by_name(pool_name)
        
        # Get coordinates for the geo-location
        location_data = GEO_LOCATIONS[geo_location]
        latitude = location_data["latitude"]
        longitude = location_data["longitude"]
        
        if pool_id:
            # Get existing origins and merge with new IP
            existing_origins = get_pool_origins(pool_id)
            
            # Check if IP already exists
            ip_exists = any(origin.get('address') == ip_address for origin in existing_origins)
            
            if ip_exists:
                logger.info(f"IP {ip_address} already exists in pool {pool_name}")
                return pool_id
            
            # Add new origin with geo-location identifier
            new_origin = {
                "name": f"origin-{geo_location}",
                "address": ip_address,
                "enabled": True,
                "weight": CONFIG['CF_ORIGIN_WEIGHT']
            }
            
            # Merge with existing origins
            updated_origins = existing_origins + [new_origin]
            
            logger.info(f"Merging IP {ip_address} from {geo_location} with {len(existing_origins)} existing origins")
            
            # Update pool
            update_data = {
                "origins": updated_origins,
                "latitude": latitude,
                "longitude": longitude
            }
            
            url = f"{CF_API_BASE}/accounts/{CONFIG['CF_ACCOUNT_ID']}/load_balancers/pools/{pool_id}"
            result = make_cloudflare_request("PUT", url, update_data)
            
            if result and result.get('success'):
                logger.info(f"Successfully updated pool {pool_name} with IP {ip_address}")
                return pool_id
            else:
                logger.error(f"Failed to update pool {pool_name}")
                return None
        else:
            # Create new pool
            origins = [{
                "name": f"origin-{geo_location}",
                "address": ip_address,
                "enabled": True,
                "weight": CONFIG['CF_ORIGIN_WEIGHT']
            }]
            
            pool_data = {
                "name": pool_name,
                "origins": origins,
                "latitude": latitude,
                "longitude": longitude
            }
            
            logger.info(f"Creating pool {pool_name} with coordinates: lat={latitude}, lng={longitude}")
            
            url = f"{CF_API_BASE}/accounts/{CONFIG['CF_ACCOUNT_ID']}/load_balancers/pools"
            result = make_cloudflare_request("POST", url, pool_data)
            
            if result and result.get('success'):
                new_pool_id = result['result']['id']
                logger.info(f"Successfully created pool {pool_name} with IP {ip_address}")
                return new_pool_id
            else:
                logger.error(f"Failed to create pool {pool_name}")
                return None
                
    except Exception as e:
        logger.error(f"Failed to create/update pool: {e}")
        return None

def find_lb_id_by_name(name: str) -> Optional[str]:
    """Find load balancer ID by hostname."""
    url = f"{CF_API_BASE}/zones/{CONFIG['CF_ZONE_ID']}/load_balancers"
    result = make_cloudflare_request("GET", url)
    
    if result and result.get('success'):
        lbs = result.get('result', [])
        for lb in lbs:
            if lb['name'] == name:
                return lb['id']
    
    return None

def create_or_update_load_balancer(pool_id: str) -> bool:
    """Create or update load balancer."""
    try:
        lb_id = find_lb_id_by_name(CONFIG['CF_LB_HOSTNAME'])
        
        lb_data = {
            "name": CONFIG['CF_LB_HOSTNAME'],
            "fallback_pool": pool_id,
            "default_pools": [pool_id],
            "proxied": True,
            "ttl": 30,
            "steering_policy": "least_connections"
        }
        
        if lb_id:
            # Update existing load balancer
            url = f"{CF_API_BASE}/zones/{CONFIG['CF_ZONE_ID']}/load_balancers/{lb_id}"
            result = make_cloudflare_request("PUT", url, lb_data)
            
            if result and result.get('success'):
                logger.info(f"Successfully updated load balancer {CONFIG['CF_LB_HOSTNAME']}")
                return True
            else:
                logger.error(f"Failed to update load balancer {CONFIG['CF_LB_HOSTNAME']}")
                return False
        else:
            # Create new load balancer
            url = f"{CF_API_BASE}/zones/{CONFIG['CF_ZONE_ID']}/load_balancers"
            result = make_cloudflare_request("POST", url, lb_data)
            
            if result and result.get('success'):
                logger.info(f"Successfully created load balancer {CONFIG['CF_LB_HOSTNAME']}")
                return True
            else:
                logger.error(f"Failed to create load balancer {CONFIG['CF_LB_HOSTNAME']}")
                return False
                
    except Exception as e:
        logger.error(f"Failed to create/update load balancer: {e}")
        return False

def setup_kubernetes_client() -> client.NetworkingV1Api:
    """Setup Kubernetes client with proper error handling."""
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes configuration")
    except config.ConfigException:
        try:
            config.load_kube_config()
            logger.info("Loaded local Kubernetes configuration")
        except config.ConfigException as e:
            logger.error(f"Failed to load Kubernetes configuration: {e}")
            sys.exit(1)
    
    return client.NetworkingV1Api()

def watch_ingresses():
    """Watch Kubernetes ingresses for changes with automatic reconnection."""
    v1_api = setup_kubernetes_client()
    
    logger.info(f"Starting to watch ingresses with label selector: {CONFIG['LABEL_SELECTOR']}")
    logger.info(f"Load balancer hostname: {CONFIG['CF_LB_HOSTNAME']}")
    logger.info(f"Supported geo-locations: {list(GEO_LOCATIONS.keys())}")
    
    while True:
        w = watch.Watch()
        try:
            logger.info("Starting/Restarting watch stream...")
            for event in w.stream(
                v1_api.list_ingress_for_all_namespaces,
                label_selector=CONFIG['LABEL_SELECTOR'],
                timeout_seconds=300  # 5 minutes timeout
            ):
                try:
                    ingress = event['object']
                    namespace = ingress.metadata.namespace
                    ingress_name = ingress.metadata.name
                    event_type = event['type']
                    
                    logger.info(f"Event: {event_type} Ingress: {namespace}/{ingress_name}")

                    if event_type in ['ADDED', 'MODIFIED']:
                        # Extract geo-location and cluster name from labels
                        geo_location = extract_geo_location_from_labels(ingress)
                        cluster_name = extract_cluster_name_from_labels(ingress)
                        
                        if not geo_location:
                            logger.warning(f"No valid geo-location found in labels for {namespace}/{ingress_name}")
                            continue
                        
                        if not cluster_name:
                            logger.warning(f"No cluster-name found in labels for {namespace}/{ingress_name}")
                            continue
                        
                        lb_ip = get_lb_ip(ingress)
                        if lb_ip:
                            logger.info(f"Load Balancer IP detected: {lb_ip}")
                            logger.info(f"Processing for geo-location: {geo_location}, cluster: {cluster_name}")
                            
                            pool_id = create_or_update_pool_with_coordination(lb_ip, geo_location, cluster_name)
                            if pool_id:
                                success = create_or_update_load_balancer(pool_id)
                                if not success:
                                    logger.error(f"Failed to update load balancer for {namespace}/{ingress_name}")
                            else:
                                logger.error(f"Failed to update pool for {namespace}/{ingress_name}")
                        else:
                            logger.debug(f"No Load Balancer IP available for {namespace}/{ingress_name}")
                    elif event_type == 'DELETED':
                        logger.info(f"Ingress {namespace}/{ingress_name} was deleted")
                        # Note: We don't automatically remove origins on ingress deletion
                        # as other ingresses might be using the same IP
                        
                except Exception as e:
                    logger.error(f"Error processing ingress event: {e}")
                    continue
                    
        except Exception as e:
            logger.warning(f"Watch stream ended: {e}")
            logger.info("Reconnecting in 5 seconds...")
            time.sleep(5)
        finally:
            try:
                w.stop()
            except:
                pass

if __name__ == "__main__":
    try:
        watch_ingresses()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)
