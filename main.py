import os
import requests
from kubernetes import client, config, watch

LABEL_SELECTOR = os.getenv("LABEL_SELECTOR", "watch=true")
CF_API_TOKEN = os.getenv("CF_API_TOKEN")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_ZONE_ID = os.getenv("CF_ZONE_ID")
CF_POOL_NAME = os.getenv("CF_POOL_NAME", "k8s-ingress-pool")
CF_LB_NAME = os.getenv("CF_LB_NAME", "k8s-ingress-lb")
CF_LB_HOSTNAME = os.getenv("CF_LB_HOSTNAME", "app.example.com")  # replace accordingly
CF_POOL_LATITUDE = os.getenv("CF_POOL_LATITUDE")
CF_POOL_LONGITUDE = os.getenv("CF_POOL_LONGITUDE")
CF_ORIGIN_WEIGHT = int(os.getenv("CF_ORIGIN_WEIGHT", "33"))

CF_API_BASE = "https://api.cloudflare.com/client/v4"

headers = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json"
}

def get_lb_ip(ingress):
    ingress_status = ingress.status.load_balancer.ingress
    if ingress_status:
        lb_entry = ingress_status[0]
        return lb_entry.ip or lb_entry.hostname
    return None

def find_pool_id_by_name(name):
    resp = requests.get(f"{CF_API_BASE}/accounts/{CF_ACCOUNT_ID}/load_balancers/pools", headers=headers)
    pools = resp.json().get('result', [])
    for pool in pools:
        if pool['name'] == name:
            return pool['id']
    return None

def create_or_update_pool(ip_address):
    pool_id = find_pool_id_by_name(CF_POOL_NAME)
    origins = [{"name": "origin-1", "address": ip_address, "enabled": True, "weight": CF_ORIGIN_WEIGHT}]
    
    # Build the base payload
    pool_data = {
        "name": CF_POOL_NAME,
        "origins": origins
    }
    
    # Add latitude and longitude if both are provided
    if CF_POOL_LATITUDE and CF_POOL_LONGITUDE:
        try:
            latitude = float(CF_POOL_LATITUDE)
            longitude = float(CF_POOL_LONGITUDE)
            pool_data["latitude"] = latitude
            pool_data["longitude"] = longitude
            print(f"Pool configured with coordinates: lat={latitude}, lng={longitude}")
        except ValueError:
            print("Warning: Invalid latitude or longitude values provided, skipping geo configuration")
    elif CF_POOL_LATITUDE or CF_POOL_LONGITUDE:
        print("Warning: Both latitude and longitude must be provided together, skipping geo configuration")
    
    if pool_id:
        # Update Pool - remove name from payload for updates
        update_data = {k: v for k, v in pool_data.items() if k != "name"}
        resp = requests.put(
            f"{CF_API_BASE}/accounts/{CF_ACCOUNT_ID}/load_balancers/pools/{pool_id}",
            json=update_data,
            headers=headers
        )
        print("Updated Pool:", resp.json())
    else:
        # Create Pool
        resp = requests.post(
            f"{CF_API_BASE}/accounts/{CF_ACCOUNT_ID}/load_balancers/pools",
            json=pool_data,
            headers=headers
        )
        if resp.json().get('success'):
            pool_id = resp.json()['result']['id']
            print("Created Pool:", resp.json())
        else:
            print("Failed to create pool:", resp.json())
            return None

    return pool_id

def find_lb_id_by_name(name):
    resp = requests.get(f"{CF_API_BASE}/zones/{CF_ZONE_ID}/load_balancers", headers=headers)
    lbs = resp.json().get('result', [])
    for lb in lbs:
        if lb['name'] == name:
            return lb['id']
    return None

def create_load_balancer(pool_id):
    data = {
        "name": CF_LB_HOSTNAME,  # The hostname to associate with the Load Balancer
        "fallback_pool": pool_id,
        "default_pools": [pool_id],
        "proxied": True,
        "ttl": 30,
        "steering_policy": "least_connections"
    }

    resp = requests.post(
        f"{CF_API_BASE}/zones/{CF_ZONE_ID}/load_balancers",
        json=data,
        headers=headers
    )
    print("Created Load Balancer:", resp.json())

def update_load_balancer(lb_id, pool_id):
    data = {
        "fallback_pool": pool_id,
        "default_pools": [pool_id],
        "proxied": True,
        "ttl": 30,
        "steering_policy": "least_connections"
    }

    resp = requests.put(
        f"{CF_API_BASE}/zones/{CF_ZONE_ID}/load_balancers/{lb_id}",
        json=data,
        headers=headers
    )
    print("Updated Load Balancer:", resp.json())

def create_or_update_load_balancer(pool_id):
    lb_id = find_lb_id_by_name(CF_LB_HOSTNAME)  # Search by hostname instead of CF_LB_NAME
    if lb_id:
        update_load_balancer(lb_id, pool_id)
    else:
        create_load_balancer(pool_id)

def handle_ingress_event(ingress):
    lb_ip = get_lb_ip(ingress)
    if lb_ip:
        print(f"Detected Load Balancer IP/Hostname: {lb_ip}")
        pool_id = create_or_update_pool(lb_ip)
        create_or_update_load_balancer(pool_id)
    else:
        print("No Load Balancer IP found yet.")

def main():
    # Validate required environment variables
    if not CF_API_TOKEN:
        print("Error: CF_API_TOKEN environment variable is required")
        return
    if not CF_ACCOUNT_ID:
        print("Error: CF_ACCOUNT_ID environment variable is required")
        return
    if not CF_ZONE_ID:
        print("Error: CF_ZONE_ID environment variable is required")
        return

    try:
        config.load_incluster_config()
    except:
        config.load_kube_config()

    networking_api = client.NetworkingV1Api()
    w = watch.Watch()
    print(f"Watching Ingress with label {LABEL_SELECTOR}...")

    for event in w.stream(networking_api.list_ingress_for_all_namespaces, label_selector=LABEL_SELECTOR):
        ingress = event['object']
        event_type = event['type']
        namespace = ingress.metadata.namespace
        ingress_name = ingress.metadata.name

        print(f"Event: {event_type} - {namespace}/{ingress_name}")
        handle_ingress_event(ingress)

if __name__ == "__main__":
    main()
