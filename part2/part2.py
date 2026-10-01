#!/usr/bin/env python3

import argparse
import os
from pprint import pprint
import time
import google.auth
from google.cloud import compute_v1

# Active App Default Credentials and Project ID
credentials, project = google.auth.default()
if not project:
    project = "lab5-510303"

zone = "us-central1-a" # CHANGE BASED ON VM FROM PART1
base_instance = "flask-instance"  # The running VM created in Part 1

# Required naming convention: base-snapshot-<instance>
snapshot_name = f"base-snapshot-{base_instance}"

# Initialize modern SDK clients
disks_client = compute_v1.DisksClient()
instances_client = compute_v1.InstancesClient()

def create_disk_snapshot(project_id, zone_name, instance_disk_name, snap_name):
    # Takes point in time snapshot of primary VM disk, cutting need for startup on new machines, cutting server replication
    
    print(f"Creating snapshot '{snap_name}' from disk '{instance_disk_name}'")
    
    # Define Snapshot object
    snapshot_resource = compute_v1.Snapshot(name=snap_name)
    
    # Call Disks API to create snapshot from base disk
    operation = disks_client.create_snapshot(
        project=project_id,
        zone=zone_name,
        disk=instance_disk_name,
        snapshot_resource=snapshot_resource
    )
    
    # Wait until GCP finishes
    operation.result()
    
    print(f"Snapshot '{snap_name}' created successfully.\n")

def clone_instance_from_snapshot(project_id, zone_name, clone_name, snap_name):
    # Clone VM via snapshot and measure performance diff

    start_time = time.time()
    compute_node_type = f"zones/{zone_name}/machineTypes/f1-micro"
    
    # Configure boot disk initialized directly from our custom snapshot URL
    disk = compute_v1.AttachedDisk(
        boot=True,
        auto_delete=True,
        initialize_params=compute_v1.AttachedDiskInitializeParams(
            source_snapshot=f"projects/{project_id}/global/snapshots/{snap_name}"
        )
    )
    
    # Attach VM to default VPC with an external IPv4 address
    network_interface = compute_v1.NetworkInterface(
        network="global/networks/default",
        access_configs=[
            compute_v1.AccessConfig(type_="ONE_TO_ONE_NAT", name="External NAT")
        ]
    )
    
    # Attach firewall tag so the 'allow-5000' rule applies automatically
    tags = compute_v1.Tags(items=["allow-5000"])
    
    # Assemble Instance resource object
    instance_resource = compute_v1.Instance(
        name=clone_name,
        machine_type=compute_node_type,
        disks=[disk],
        network_interfaces=[network_interface],
        tags=tags
    )
    
    print(f"Provisioning clone '{clone_name}' from snapshot")
    operation = instances_client.insert(
        project=project_id,
        zone=zone_name,
        instance_resource=instance_resource
    )
    
    # Wait for GCP completion and calculate total elapsed seconds
    operation.result()
    elapsed_time = time.time() - start_time
    print(f"Clone '{clone_name}' finished in {elapsed_time:.2f} seconds.")
    return elapsed_time

if __name__ == "__main__":
    # 1. Look up the boot disk name from the instance
    instance = instances_client.get(project=project, zone=zone, instance=base_instance)
    disk_name = instance.disks[0].source.split("/")[-1]
    
    # Step A: Capture the point in time snapshot
    create_disk_snapshot(project, zone, disk_name, snapshot_name)
    
    # Step B: Spin up a trio of cloned instances and record setup durations
    clones = ["clone-vm-1", "clone-vm-2", "clone-vm-3"]
    timing_results = {}
    
    for clone in clones:
        duration = clone_instance_from_snapshot(project, zone, clone, snapshot_name)
        timing_results[clone] = duration
        
    # Step C: Write performance metrics to TIMING.md
    print("\nWriting performance metrics to TIMING.md...")
    with open("TIMING.md", "w") as f:
        f.write("# Part 2: Snapshot Cloning Performance Metrics\n\n")
        f.write("| Instance Name | Creation Time (Seconds) |\n")
        f.write("| --- | --- |\n")
        for vm_name, duration in timing_results.items():
            f.write(f"| {vm_name} | {duration:.2f}s |\n")
            
    print("TIMING.md successfully generated!")