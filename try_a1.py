#!/usr/bin/env python3
"""One safe Oracle A1 Always Free launch attempt; intended for scheduled GitHub Actions.

This script NEVER creates a paid shape, never upgrades an account and never
adds volumes. It creates at most one A1 (1 OCPU, 6 GB RAM, 50 GB boot volume)
when capacity permits, and skips launch if an A1 already exists in root.
"""

import configparser
import os
import sys
import tempfile
import uuid
from pathlib import Path

import oci

REGION = "ap-singapore-2"  # Singapore West, account home region
SHAPE = "VM.Standard.A1.Flex"
OCPUS = 1.0
MEMORY_GB = 6.0
NAME = "VPS-A"


def finish(result, exit_code=0):
    """Publish the machine-readable outcome to the workflow notification step."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as file:
            file.write(f"result={result}\n")
    return exit_code


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing GitHub Actions secret: {name}")
    return value


def main():
    config_text = required("OCI_CONFIG_TEXT")
    private_key_text = required("OCI_PRIVATE_KEY")
    ssh_pubkey = required("OCI_SSH_PUBLIC_KEY")
    subnet_id = required("OCI_SUBNET_OCID")

    if not subnet_id.startswith("ocid1.subnet."):
        raise ValueError("OCI_SUBNET_OCID must be a subnet OCID, not a VCN OCID")
    if not ssh_pubkey.startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-")):
        raise ValueError("OCI_SSH_PUBLIC_KEY must be the content of an SSH .pub file")
    if "BEGIN" not in private_key_text or "PRIVATE KEY" not in private_key_text:
        raise ValueError("OCI_PRIVATE_KEY must contain the Oracle API signing .pem private key")

    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(config_text)
    if "DEFAULT" not in parser:
        raise ValueError("OCI_CONFIG_TEXT requires a [DEFAULT] block")
    config = dict(parser["DEFAULT"])
    if config.get("region") != REGION:
        raise ValueError("OCI API config region must be ap-singapore-2 (Singapore West)")
    for field in ("user", "tenancy", "fingerprint"):
        if not config.get(field):
            raise ValueError(f"OCI_CONFIG_TEXT missing {field}")

    with tempfile.TemporaryDirectory(prefix="a1-api-") as temp_dir:
        key_file = Path(temp_dir) / "api_key.pem"
        key_file.write_text(private_key_text + "\n", encoding="utf-8")
        key_file.chmod(0o600)
        config["key_file"] = str(key_file)
        oci.config.validate_config(config)

        tenancy_id = config["tenancy"]   # User's compartment in screenshots: root
        compute = oci.core.ComputeClient(config)
        network = oci.core.VirtualNetworkClient(config)
        identity = oci.identity.IdentityClient(config)

        # Refuse to create a duplicate A1, even if it was made manually.
        instances = oci.pagination.list_call_get_all_results(
            compute.list_instances, compartment_id=tenancy_id
        ).data
        current_a1 = [instance for instance in instances
                      if instance.shape == SHAPE and
                      instance.lifecycle_state not in ("TERMINATED", "TERMINATING")]
        if current_a1:
            for instance in current_a1:
                print(f"ALREADY_EXISTS: {instance.display_name}, status={instance.lifecycle_state}, OCID={instance.id}")
            print("STOP: An A1.Flex already exists. No new instance requested.")
            return finish("ALREADY_EXISTS")

        subnet = network.get_subnet(subnet_id).data
        if subnet.prohibit_public_ip_on_vnic:
            raise ValueError("Selected subnet is private; use a PUBLIC subnet OCID")
        if subnet.compartment_id != tenancy_id:
            raise ValueError("This simple kit expects the public subnet in the root compartment")

        ads = identity.list_availability_domains(compartment_id=tenancy_id).data
        if len(ads) != 1:
            raise ValueError(f"Expected 1 availability domain in Singapore West, got {len(ads)}")
        availability_domain = ads[0].name

        # Shape filtering ensures ARM64 compatibility. Fetch latest platform image.
        images = oci.pagination.list_call_get_all_results(
            compute.list_images,
            compartment_id=tenancy_id,
            operating_system="Canonical Ubuntu",
            operating_system_version="24.04",
            shape=SHAPE,
            lifecycle_state="AVAILABLE",
        ).data
        if not images:
            raise ValueError("No Ubuntu 24.04 image compatible with A1.Flex found; no launch attempted")
        # Oracle's `shape=A1.Flex` filter is the compatibility guarantee;
        # prefer an explicitly ARM-labeled image where available.
        explicit_arm = [im for im in images if any(
            token in (im.display_name or "").lower() for token in ("aarch64", "arm64")
        )]
        candidates = explicit_arm or images
        image = sorted(candidates, key=lambda im: str(im.time_created), reverse=True)[0]

        print(f"TRY: {REGION} {availability_domain} {SHAPE}, {OCPUS:g} OCPU, {MEMORY_GB:g} GB, 50 GB, Ubuntu 24.04 ARM64")
        print(f"IMAGE: {image.display_name}")
        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=tenancy_id,
            availability_domain=availability_domain,
            display_name=NAME,
            shape=SHAPE,
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=OCPUS, memory_in_gbs=MEMORY_GB,
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                image_id=image.id,
                boot_volume_size_in_gbs=50,
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=subnet_id, assign_public_ip=True,
            ),
            metadata={"ssh_authorized_keys": ssh_pubkey},
        )
        try:
            response = compute.launch_instance(
                details,
                opc_retry_token=str(uuid.uuid4()),
                retry_strategy=oci.retry.NoneRetryStrategy(),
            )
        except oci.exceptions.ServiceError as error:
            message = (error.message or "").lower()
            if "out of host capacity" in message or "out of capacity" in message:
                print("NO_CAPACITY: Singapore West AD-1 is full. Next scheduled run will try again.")
                return finish("NO_CAPACITY")
            if error.status == 429:
                print("RATE_LIMITED: Oracle asked us to slow down. Next scheduled run will try again.")
                return finish("RATE_LIMITED")
            print(f"ORACLE_ERROR: HTTP {error.status}, code={error.code}, message={error.message}")
            return finish("ORACLE_ERROR", 1)

        instance = response.data
        print(f"INSTANCE_CREATED: {instance.id}")
        print(f"INSTANCE_NAME: {instance.display_name}")
        print(f"STATUS: {instance.lifecycle_state}. Check Oracle Compute > Instances for RUNNING / Public IP.")
        print("Future runs will see the existing A1 and will not create another. Disable the workflow when ready.")
        return finish("INSTANCE_CREATED")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"SETUP_ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(finish("SETUP_ERROR", 1))
