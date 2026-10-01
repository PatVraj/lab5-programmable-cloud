#!/bin/bash
set -x

echo "Deleting lab compute instances..."
gcloud compute instances delete \
    flask-instance \
    clone-vm-1 \
    clone-vm-2 \
    clone-vm-3 \
    vm1-launcher \
    flask-instance-vm2 \
    --zone=us-central1-a \
    --quiet

echo "Deleting snapshot..."
gcloud compute snapshots delete base-snapshot-flask-instance --quiet

echo "Deleting orphaned unattached disks..."
UNATTACHED_DISKS=$(gcloud compute disks list --filter="-users:*" --format="value(name)")
if [ -n "$UNATTACHED_DISKS" ]; then
    echo "$UNATTACHED_DISKS" | xargs -I {} gcloud compute disks delete {} --zone=us-central1-a --quiet
fi

echo "Deleting lab firewall rule..."
gcloud compute firewall-rules delete allow-5000 --quiet

echo "Cleanup complete! All lab resources removed."
