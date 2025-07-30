#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import time
import json
import logging
from typing import Dict, List, Optional
import requests
from kubernetes import client, config, watch

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Cloudflare API base URL
CF_API_BASE = "https://api.cloudflare.com/client/v4"

# Predefined geo-location coordinates
GEO_LOCATIONS = {
    "eu": {"name": "Europe", "latitude": 50.1109, "longitude": 8.6821},
    "us_east": {"name": "United States East", "latitude": 40.7128, "longitude": -74.0060},
    "us_west": {"name": "United States West", "latitude": 34.0522, "longitude": -118.2437},
    "asia": {"name": "Asia", "latitude": 35.6762, "longitude": 139.6503}
}

def validate_env_vars() -> Dict[str, str]:
    """Validate and return required environment variables."""
    required_vars = ['CF_API_TOKEN', 'CF_ACCOUNT_ID', 'CF_ZONE_ID', 'GEO_LOCATION']
    missing_vars = []
    
    config = {}
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
        else:
            config[var] = value
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        sys.exit(1)
    
    # Validate GEO_LOCATION
    if config['GEO_LOCATION'] not in GEO_LOCATIONS:
        logger.error(f"Invalid GEO_LOCATION '{config['GEO_LOCATION']}'. Must be one of: {list(GEO_LOCATIONS.keys())}")
        sys.exit(1)
    
    # Set optional variables with defaults
    config['CF_LB_HOSTNAME'] = os.getenv('CF_LB_HOSTNAME', 'app.example.com')
    config['CF_ORIGIN_WEIGHT'] = float(os.getenv('CF_ORIGIN_WEIGHT', '33'))
    config['LABEL_SELECTOR'] = os.getenv('LABEL_SELECTOR', 'dns.external/geo-route=true')
    
    # Validate CF_ORIGIN_WEIGHT
    try:
        weight = config['CF_ORIGIN_WEIGHT']
        if weight < 0 or weight > 100:
            raise ValueError("Weight must be between 0 and 100")
    except ValueError as e:
        logger.error(f"Invalid CF_ORIGIN_WEIGHT value: {e}")
        sys.exit(1)
    
    return config

# Load configuration
try:
    CONFIG = validate_env_vars()
    logger.info(f"Configuration loaded successfully")
    logger.info(f"Geo-location: {CONFIG['GEO_LOCATION']} ({GEO_LOCATIONS[CONFIG['GEO_LOCATION']]['name']})")
    logger.info(f"Load balancer hostname: {CONFIG['CF_LB_HOSTNAME']}")
    logger.info(f"Supported geo-locations: {list(GEO_LOCATIONS.keys())}")
    
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
    """Build pool name from cluster name with geo location suffix."""
    return f"k8s-pool-{cluster_name}-{CONFIG['GEO_LOCATION']}"

def make_cloudflare_request(method: str, url: str, data: Dict = None) -> Optional[Dict]:
    """Make a Cloudflare API request with proper error handling."""
    try:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {CONFIG['CF_API_TOKEN']}"
        }
        
        # Log the request details
        logger.info(f"Making {method.upper()} request to: {url}")
        if data:
            logger.info(f"Request data: {data}")
        
        # Prepare JSON data with explicit UTF-8 encoding
        json_data = None
        if data:
            json_data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=30)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, data=json_data, timeout=30)
        elif method.upper() == "PUT":
            response = requests.put(url, headers=headers, data=json_data, timeout=30)
        elif method.upper() == "DELETE":
            response = requests.delete(url, headers=headers, timeout=30)
        else:
            logger.error(f"Unsupported HTTP method: {method}")
            return None
        
        # Log the response details
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response headers: {dict(response.headers)}")
        logger.info(f"Response body: {response.text}")
        
        if response.status_code in [200, 201]:
            logger.info(f"Cloudflare API request successful: {response.status_code}")
            return response.json()
        else:
            logger.error(f"Cloudflare API request failed: {response.status_code} - {response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in request: {e}")
        return None

def find_pool_id_by_name(name: str) -> Optional[str]:
    """Find pool ID by name."""
    logger.info(f"Searching for pool with name: {name}")
    url = f"{CF_API_BASE}/accounts/{CONFIG['CF_ACCOUNT_ID']}/load_balancers/pools"
    result = make_cloudflare_request("GET", url)
    
    if result and result.get('success'):
        pools = result.get('result', [])
        logger.info(f"Found {len(pools)} pools in account")
        for pool in pools:
            logger.info(f"Pool: {pool['name']} (ID: {pool['id']})")
            if pool['name'] == name:
                logger.info(f"Found matching pool: {name} with ID: {pool['id']}")
                return pool['id']
        logger.info(f"No pool found with name: {name}")
    else:
        logger.error(f"Failed to retrieve pools from Cloudflare API")
    
    return None

def get_pool_origins(pool_id: str) -> List[Dict]:
    """Get current origins from a pool."""
    logger.info(f"Retrieving origins for pool ID: {pool_id}")
    url = f"{CF_API_BASE}/accounts/{CONFIG['CF_ACCOUNT_ID']}/load_balancers/pools/{pool_id}"
    result = make_cloudflare_request("GET", url)
    
    if result and result.get('success'):
        origins = result['result'].get('origins', [])
        logger.info(f"Found {len(origins)} origins in pool {pool_id}")
        for origin in origins:
            logger.info(f"Origin: {origin.get('name', 'unnamed')} - {origin.get('address', 'no-address')} (enabled: {origin.get('enabled', False)})")
        return origins
    else:
        logger.error(f"Failed to retrieve origins for pool {pool_id}")
    
    return []

def create_or_update_pool_with_coordination(ip_address: str, cluster_name: str) -> Optional[str]:
    """Create or update pool with multi-cluster coordination."""
    try:
        pool_name = build_pool_name(cluster_name)
        logger.info(f"Processing pool operation for cluster: {cluster_name}, pool: {pool_name}, IP: {ip_address}")
        
        pool_id = find_pool_id_by_name(pool_name)
        
        # Get coordinates for the geo-location
        location_data = GEO_LOCATIONS[CONFIG['GEO_LOCATION']]
        latitude = location_data["latitude"]
        longitude = location_data["longitude"]
        logger.info(f"Using coordinates: lat={latitude}, lng={longitude} for geo-location: {CONFIG['GEO_LOCATION']}")
        
        if pool_id:
            logger.info(f"Pool {pool_name} exists (ID: {pool_id}), checking for IP {ip_address}")
            # Get existing origins and merge with new IP
            existing_origins = get_pool_origins(pool_id)
            
            # Check if IP already exists
            ip_exists = any(origin.get('address') == ip_address for origin in existing_origins)
            
            if ip_exists:
                logger.info(f"IP {ip_address} already exists in pool {pool_name}")
                return pool_id
            
            # Add new origin with geo-location identifier
            new_origin = {
                "name": f"origin-{CONFIG['GEO_LOCATION']}",
                "address": ip_address,
                "enabled": True,
                "weight": CONFIG['CF_ORIGIN_WEIGHT']
            }
            
            # Merge with existing origins
            updated_origins = existing_origins + [new_origin]
            
            logger.info(f"Merging IP {ip_address} from {CONFIG['GEO_LOCATION']} with {len(existing_origins)} existing origins")
            
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
                logger.error(f"Result: {result}")
                return None
        else:
            logger.info(f"Pool {pool_name} does not exist, creating new pool")
            # Create new pool
            origins = [{
                "name": f"origin-{CONFIG['GEO_LOCATION']}",
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
                logger.info(f"Successfully created pool {pool_name} with IP {ip_address} (ID: {new_pool_id})")
                return new_pool_id
            else:
                logger.error(f"Failed to create pool {pool_name}")
                logger.error(f"Result: {result}")
                return None
                
    except Exception as e:
        logger.error(f"Failed to create/update pool: {e}")
        return None

def find_lb_id_by_name(name: str) -> Optional[str]:
    """Find load balancer ID by hostname."""
    logger.info(f"Searching for load balancer with name: {name}")
    url = f"{CF_API_BASE}/zones/{CONFIG['CF_ZONE_ID']}/load_balancers"
    result = make_cloudflare_request("GET", url)
    
    if result and result.get('success'):
        lbs = result.get('result', [])
        logger.info(f"Found {len(lbs)} load balancers in zone")
        for lb in lbs:
            logger.info(f"Load Balancer: {lb['name']} (ID: {lb['id']})")
            if lb['name'] == name:
                logger.info(f"Found matching load balancer: {name} with ID: {lb['id']}")
                return lb['id']
        logger.info(f"No load balancer found with name: {name}")
    else:
        logger.error(f"Failed to retrieve load balancers from Cloudflare API")
    
    return None

def create_or_update_load_balancer(pool_id: str) -> bool:
    """Create or update load balancer."""
    try:
        logger.info(f"Processing load balancer operation for hostname: {CONFIG['CF_LB_HOSTNAME']}, pool ID: {pool_id}")
        
        lb_id = find_lb_id_by_name(CONFIG['CF_LB_HOSTNAME'])
        
        lb_data = {
            "name": CONFIG['CF_LB_HOSTNAME'],
            "fallback_pool": pool_id,
            "default_pools": [pool_id],
            "proxied": True,
            "ttl": 30,
            "steering_policy": "least_connections"
        }
        
        logger.info(f"Load balancer configuration: {lb_data}")
        
        if lb_id:
            logger.info(f"Load balancer {CONFIG['CF_LB_HOSTNAME']} exists (ID: {lb_id}), updating...")
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
            logger.info(f"Load balancer {CONFIG['CF_LB_HOSTNAME']} does not exist, creating new load balancer...")
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
    logger.info(f"Geo-location: {CONFIG['GEO_LOCATION']} ({GEO_LOCATIONS[CONFIG['GEO_LOCATION']]['name']})")
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
                        # Extract cluster name from labels
                        cluster_name = extract_cluster_name_from_labels(ingress)
                        
                        if not cluster_name:
                            logger.warning(f"No cluster-name found in labels for {namespace}/{ingress_name}")
                            continue
                        
                        lb_ip = get_lb_ip(ingress)
                        if lb_ip:
                            logger.info(f"Load Balancer IP detected: {lb_ip}")
                            logger.info(f"Processing for geo-location: {CONFIG['GEO_LOCATION']}, cluster: {cluster_name}")
                            
                            pool_id = create_or_update_pool_with_coordination(lb_ip, cluster_name)
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
