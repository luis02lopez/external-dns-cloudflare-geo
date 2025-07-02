import os
import requests
from kubernetes import client, config, watch

LABEL_SELECTOR = os.getenv("LABEL_SELECTOR", "watch=true")
CF_API_TOKEN = os.getenv("CF_API_TOKEN")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_POOL_NAME = os.getenv("CF_POOL_NAME", "k8s-ingress-pool")
CF_LB_NAME = os.getenv("CF_LB_NAME", "k8s-ingress-lb")
CF_LB_HOSTNAME = os.getenv("CF_LB_HOSTNAME", "app.example.com")  # replace accordingly

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
    origins = [{"name": "origin-1", "address": ip_address, "enabled": True}]
    
    if pool_id:
        # Update Pool
        resp = requests.put(
            f"{CF_API_BASE}/accounts/{CF_ACCOUNT_ID}/load_balancers/pools/{pool_id}",
            json={"origins": origins},
            headers=headers
        )
        print("Updated Pool:", resp.json())
    else:
        # Create Pool
        resp = requests.post(
            f"{CF_API_BASE}/accounts/{CF_ACCOUNT_ID}/load_balancers/pools",
            json={"name": CF_POOL_NAME, "origins": origins},
            headers=headers
        )
        pool_id = resp.json()['result']['id']
        print("Created Pool:", resp.json())

    return pool_id

def find_lb_id_by_name(name):
    resp = requests.get(f"{CF_API_BASE}/accounts/{CF_ACCOUNT_ID}/load_balancers", headers=headers)
    lbs = resp.json().get('result', [])
    for lb in lbs:
        if lb['name'] == name:
            return lb['id']
    return None

def create_load_balancer(pool_id):
    data = {
        "name": CF_LB_NAME,
        "fallback_pool": pool_id,
        "default_pools": [pool_id],
        "proxied": True,
        "ttl": 30,
        "steering_policy": "dynamic_latency",
        "hosts": [CF_LB_HOSTNAME]
    }

    resp = requests.post(
        f"{CF_API_BASE}/accounts/{CF_ACCOUNT_ID}/load_balancers",
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
        "steering_policy": "dynamic_latency",
        "hosts": [CF_LB_HOSTNAME]
    }

    resp = requests.put(
        f"{CF_API_BASE}/accounts/{CF_ACCOUNT_ID}/load_balancers/{lb_id}",
        json=data,
        headers=headers
    )
    print("Updated Load Balancer:", resp.json())

def create_or_update_load_balancer(pool_id):
    lb_id = find_lb_id_by_name(CF_LB_NAME)
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
