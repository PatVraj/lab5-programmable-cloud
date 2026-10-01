#!/usr/bin/env python3

import argparse
import os
import time
from pprint import pprint

from google.api_core.exceptions import GoogleAPICallError, ServiceUnavailable
from google.cloud import compute_v1
import google.oauth2.service_account as service_account

# part3.py
#  1. setup_firewall()        -> Verify/create the 'allow-5000' rule
#  2. create_vm1_launcher()    -> Provision VM-1 and inject credentials + child code via metadata
#  3. wait_for_vm2()          -> Monitor GCP until VM-1 finishes provisioning VM-2
#  4. main execution          -> Coordinate deployment and output VM-2's Flask URL

KEY_FILE = "service-credentials.json"

if not os.path.exists(KEY_FILE):
    raise FileNotFoundError(
        f"Missing '{KEY_FILE}'. Download your service account key into this directory."
    )

# Authenticate using the Service Account JSON key
credentials = service_account.Credentials.from_service_account_file(filename=KEY_FILE)
project = credentials.project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or "lab5-510303"

# Initialize modern clients authenticated with the Service Account
instances_client = compute_v1.InstancesClient(credentials=credentials)
firewalls_client = compute_v1.FirewallsClient(credentials=credentials)

def list_instances(project_id, zone_name):
    try:
        return list(instances_client.list(project=project_id, zone=zone_name))
    except Exception as e:
        print(f"Warning: Could not list instances in {zone_name}: {e}")
        return []

def setup_firewall(project_id):
    # Firewall Rule created using Firewall Obj
    firewall_rule = compute_v1.Firewall(
        name="allow-5000",
        target_tags=["allow-5000"],
        source_ranges=["0.0.0.0/0"],
        allowed=[compute_v1.Allowed(I_p_protocol="tcp", ports=["5000"])],
    )

    # Check if rule exists
    rule_exists = False
    for rule in firewalls_client.list(project=project_id):
        if rule.name == "allow-5000":
            rule_exists = True
            break

    # Only create the rule if it was NOT found after inspecting all rules
    if not rule_exists:
        print("Creating firewall rule 'allow-5000'")
        operation = firewalls_client.insert(project=project_id, firewall_resource=firewall_rule)
        operation.result()  # Wait for GCP to finish


def create_vm1_launcher(project_id, zone_name, vm1_name, vm2_name):
    # 1. Read the service account JSON to forward it inside VM-1's metadata
    with open(KEY_FILE, "r") as f:
        sa_credentials_content = f.read()

    # 2. Flask startup script executed inside VM-2
    vm2_startup_script = """#!/bin/bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-dev git

git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial /flask-tutorial
cd /flask-tutorial

pip3 install -e .

export FLASK_APP=flaskr
flask init-db
nohup flask run --host=0.0.0.0 --port=5000 > /tmp/flask.log 2>&1 &
"""

    # 3. Python program executed on VM-1 to provision VM-2
    vm1_launch_code_template = """#!/usr/bin/env python3
import time
from google.cloud import compute_v1
import google.oauth2.service_account as service_account

# Load the forwarded Service Account credentials
CRED_PATH = "/srv/service-credentials.json"
credentials = service_account.Credentials.from_service_account_file(CRED_PATH)
project_id = credentials.project_id
zone = "__ZONE__"
target_vm = "__VM2_NAME__"

instances_client = compute_v1.InstancesClient(credentials=credentials)

# Read startup script prepared for VM-2
with open("/srv/vm2-startup-script.sh", "r") as f:
    vm2_startup = f.read()

# Build VM-2 resources
machine_type = f"zones/{zone}/machineTypes/f1-micro"
disk = compute_v1.AttachedDisk(
    boot=True,
    auto_delete=True,
    initialize_params=compute_v1.AttachedDiskInitializeParams(
        source_image="projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
    ),
)
network_interface = compute_v1.NetworkInterface(
    network="global/networks/default",
    access_configs=[
        compute_v1.AccessConfig(type_="ONE_TO_ONE_NAT", name="External NAT")
    ],
)
tags = compute_v1.Tags(items=["allow-5000"])
metadata = compute_v1.Metadata(
    items=[compute_v1.Items(key="startup-script", value=vm2_startup)]
)

instance_resource = compute_v1.Instance(
    name=target_vm,
    machine_type=machine_type,
    disks=[disk],
    network_interfaces=[network_interface],
    tags=tags,
    metadata=metadata,
)

print(f"[VM-1] Launching child VM '{target_vm}' in {zone}")
op = instances_client.insert(project=project_id, zone=zone, instance_resource=instance_resource)
op.result()
print(f"[VM-1] Child VM '{target_vm}' created successfully!")
"""
    vm1_launch_code = vm1_launch_code_template.replace("__ZONE__", zone_name).replace("__VM2_NAME__", vm2_name)

    # 4. VM-1 bash startup script: fetch metadata payloads, install SDK, and run launcher
    vm1_startup_script = """#!/bin/bash
mkdir -p /srv
cd /srv

# Download payload files stored in VM-1 metadata attributes
curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/service-credentials > /srv/service-credentials.json
curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/vm1-launch-vm2-code > /srv/vm1-launch-vm2-code.py
curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/vm2-startup-script > /srv/vm2-startup-script.sh

# Install pip and the modern Google Cloud Compute client library
apt-get update
apt-get install -y python3-pip
pip3 install google-cloud-compute google-auth

# Run child VM launcher script and pipe logs
python3 /srv/vm1-launch-vm2-code.py > /srv/launcher.log 2>&1
"""

    # 5. Configure URL to setup vCPUs and mem
    machine_type = f"zones/{zone_name}/machineTypes/f1-micro"

    # 6. Configure Boot Disk
    disk = compute_v1.AttachedDisk(
        boot=True,
        auto_delete=True,
        initialize_params=compute_v1.AttachedDiskInitializeParams(
            source_image="projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
        ),
    )

    # 7. Configure Networking with Public IP
    network_interface = compute_v1.NetworkInterface(
        network="global/networks/default",
        access_configs=[
            compute_v1.AccessConfig(type_="ONE_TO_ONE_NAT", name="External NAT")
        ],
    )

    # 8. Configure Metadata with child payloads
    metadata = compute_v1.Metadata(
        items=[
            compute_v1.Items(key="startup-script", value=vm1_startup_script),
            compute_v1.Items(key="service-credentials", value=sa_credentials_content),
            compute_v1.Items(key="vm1-launch-vm2-code", value=vm1_launch_code),
            compute_v1.Items(key="vm2-startup-script", value=vm2_startup_script),
        ]
    )

    # 9. Assemble VM-1 Instance resource object
    instance_resource = compute_v1.Instance(
        name=vm1_name,
        machine_type=machine_type,
        disks=[disk],
        network_interfaces=[network_interface],
        metadata=metadata,
    )

    print(f"Creating launcher instance '{vm1_name}' in {zone_name}")
    operation = instances_client.insert(
        project=project_id,
        zone=zone_name,
        instance_resource=instance_resource,
    )
    operation.result()  # Wait for VM-1 to finish creation
    print(f"Instance '{vm1_name}' created successfully.")


def wait_for_vm2(project_id, zone_name, vm2_name, timeout_seconds=300):
    # Poll GCP until VM-1 finishes provisioning VM-2
    print(f"\nWaiting for VM-1 to execute startup script and create '{vm2_name}'")
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        try:
            inst = instances_client.get(project=project_id, zone=zone_name, instance=vm2_name)
            if inst and inst.network_interfaces:
                public_ip = inst.network_interfaces[0].access_configs[0].nat_i_p
                print(f"Detected child VM '{vm2_name}'!")
                return public_ip
        except Exception:
            pass  # Instance not yet created by VM-1; continue polling

        print("  ...VM-1 installing packages and spawning VM-2 (waiting 15s)")
        time.sleep(15)

    raise TimeoutError(f"Timed out waiting for '{vm2_name}' to appear.")


if __name__ == "__main__":
    candidate_zones = [
        "us-central1-a",
        "us-central1-b",
        "us-central1-c",
        "us-west1-b",
        "us-west1-a",
        "us-east1-b",
    ]

    vm1_target_name = "vm1-launcher"
    vm2_target_name = "flask-instance-vm2"

    print(f"Using Service Account credentials from: {KEY_FILE}")
    print(f"Target GCP Project ID: {project}\n")

    # Display running instances in the primary zone (replacing starter stub)
    print("Your running instances in us-central1-a are:")
    for inst in list_instances(project, "us-central1-a"):
        print(f" - {inst.name} ({inst.status})")
    print()

    # Step 1: Ensure the allow-5000 firewall rule exists
    setup_firewall(project)

    # Step 2: Provision VM-1 with candidate fallback zones
    successful_zone = None
    for zone in candidate_zones:
        try:
            create_vm1_launcher(project, zone, vm1_target_name, vm2_target_name)
            successful_zone = zone
            break
        except (ServiceUnavailable, GoogleAPICallError) as e:
            print(f"Zone {zone} unavailable, attempting fallback...")

    if not successful_zone:
        raise SystemExit("All candidate zones exhausted.")

    # Step 3: Monitor until VM-1 launches VM-2 and retrieve its IP
    vm2_public_ip = wait_for_vm2(project, successful_zone, vm2_target_name)

    print("\nPart 3 Provision Complete")
    print(f"VM-1 (Launcher): {vm1_target_name} ({successful_zone})")
    print(f"VM-2 (App Host): {vm2_target_name} ({successful_zone})")
    print(f"VM-2 Flask Application URL: http://{vm2_public_ip}:5000")