#!/usr/bin/env python3

import argparse
import os
import time
from pprint import pprint

# Automatically get your active GCP project ID using default credentials
import google.auth
from google.cloud import compute_v1

# Autoretrieve active project ID
credentials, project = google.auth.default()

# Initialize modern clients (auto detect App Default Credentials)
instances_client = compute_v1.InstancesClient()
firewalls_client = compute_v1.FirewallsClient()

# part1.py
#  1. setup_firewall()   -> Check/create the 'allow-5000' rule
#  2. create_instance()  -> Build & launch the VM with the startup script
#  3. main execution      -> Coordinate calls and print http://<EXTERNAL_IP>:5000

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

def create_instance(project_id, zone, vm_name):
    # 1. Bash startup script
    startup_script = """#!/bin/bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-dev git

git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial
cd flask-tutorial

pip3 install -e .

export FLASK_APP=flaskr
flask init-db
nohup flask run --host=0.0.0.0 --port=5000 > flask.log 2>&1 &
"""

    # 2. Configure URL to setup vCPUs and mem
    machine_type = f"zones/{zone}/machineTypes/f1-micro"

    # 3. Configure Boot Disk
    disk = compute_v1.AttachedDisk(
        boot=True,
        auto_delete=True,
        initialize_params=compute_v1.AttachedDiskInitializeParams(
            source_image="projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
        ),
    )

    # 4. Configure Networking with Public IP
    network_interface = compute_v1.NetworkInterface(
        network="global/networks/default",
        access_configs=[
            compute_v1.AccessConfig(
                type_="ONE_TO_ONE_NAT",
                name="External NAT",
            )
        ],
    )

    # 5. Configure Network Tags
    tags = compute_v1.Tags(items=["allow-5000"])

    # 6. Configure Metadata for Startup Script
    metadata = compute_v1.Metadata(
        items=[
            compute_v1.Items(key="startup-script", value=startup_script)
        ]
    )

    # 7. Assemble the Instance Resource Object
    instance_resource = compute_v1.Instance(
        name=vm_name,
        machine_type=machine_type,
        disks=[disk],
        network_interfaces=[network_interface],
        tags=tags,
        metadata=metadata,
    )

    print(f"Creating instance '{vm_name}' in {zone}")
    operation = instances_client.insert(
        project=project_id, 
        zone=zone, 
        instance_resource=instance_resource
    )
    operation.result()  # Wait for instance creation
    print(f"Instance '{vm_name}' created successfully.")


from google.api_core.exceptions import GoogleAPICallError, ServiceUnavailable

if __name__ == "__main__":
    candidate_zones = [
        "us-west1-b",
        "us-west1-a",
        "us-west1-c",
        "us-central1-a",
        "us-central1-b",
        "us-central1-c",
        "us-east1-b",
    ]
    
    target_vm_name = "flask-instance"

    # 1. Check and create the firewall rule
    setup_firewall(project)

    # Loop through zones until one succeeds
    successful_zone = None
    for zone in candidate_zones:
        try:
            # 2. Provision the VM instance
            create_instance(project, zone, target_vm_name)
            successful_zone = zone
            break
        except (ServiceUnavailable, GoogleAPICallError) as e:
            print(f"Zone {zone} unavailable, attempting fallback")

    if not successful_zone:
        raise SystemExit("All zones exhausted")

    # Retrieve external IP from the winning zone and print the url
    instance_info = instances_client.get(
        project=project, zone=successful_zone, instance=target_vm_name
    )
    public_ip = instance_info.network_interfaces[0].access_configs[0].nat_i_p

    print(f"Flask Application URL: http://{public_ip}:5000")